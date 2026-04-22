from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

_RETRYABLE_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "RequestTimeout",
        "InternalError",
        "ServiceUnavailable",
        "SlowDown",
    }
)
_MAX_ATTEMPTS = 3  # initial attempt + 2 retries

# Jira-style story IDs: leading letter, letters/digits/underscores, dash, digits.
# Rejecting anything looser prevents path traversal ("..") and separator-injection
# ("foo/bar") at the S3 key boundary.
_STORY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")


class S3UploadError(Exception):
    """Raised when an upload to S3 fails (after any retries)."""

    def __init__(self, bucket: str, key: str, code: str | None, message: str) -> None:
        self.bucket = bucket
        self.key = key
        self.code = code
        super().__init__(
            f"S3 upload failed for s3://{bucket}/{key} "
            f"(code={code!r}): {message}"
        )


class S3PresignError(Exception):
    """Raised when generating a presigned URL fails."""

    def __init__(self, key: str, code: str | None, message: str) -> None:
        self.key = key
        self.code = code
        super().__init__(
            f"S3 presign failed for key={key!r} (code={code!r}): {message}"
        )


class S3Service:
    def __init__(
        self,
        bucket: str,
        *,
        client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._bucket = bucket
        if client is not None:
            self._client = client
        else:
            import boto3  # noqa: PLC0415 — lazy import to avoid cold-start overhead

            self._client = boto3.client("s3")
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self._sleep = sleep if sleep is not None else time.sleep

    def _build_key(self, story_id: str) -> str:
        date_part = self._clock().strftime("%Y-%m-%d")
        return f"specs/{story_id}/{date_part}/SPEC.md"

    def upload_spec(self, story_id: str, spec_markdown: str) -> str:
        if not _STORY_ID_RE.match(story_id):
            raise ValueError(
                f"Invalid story_id {story_id!r}: expected format "
                "'<PROJECT>-<NUMBER>' (e.g. 'SPEC-1')"
            )
        key = self._build_key(story_id)
        last_code: str | None = None
        last_message = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=spec_markdown.encode("utf-8"),
                    ContentType="text/markdown",
                )
                return key
            except ClientError as exc:
                # ClientError is a subclass of BotoCoreError, so this branch must
                # come first to preserve the AWS error-code semantics.
                code = exc.response.get("Error", {}).get("Code")
                message = exc.response.get("Error", {}).get("Message", str(exc))
                last_code = code
                last_message = message
                if code not in _RETRYABLE_CODES:
                    raise S3UploadError(self._bucket, key, code, message) from exc
                if attempt >= _MAX_ATTEMPTS - 1:
                    break
                self._sleep(0.1 * (2 ** attempt))
            except BotoCoreError as exc:
                # Network-level failures (EndpointConnectionError, ReadTimeoutError,
                # ConnectTimeoutError, …) have no AWS error code and are always
                # treated as retryable transient errors.
                last_code = type(exc).__name__
                last_message = str(exc)
                if attempt >= _MAX_ATTEMPTS - 1:
                    break
                self._sleep(0.1 * (2 ** attempt))
        raise S3UploadError(self._bucket, key, last_code, last_message)

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            message = exc.response.get("Error", {}).get("Message", str(exc))
            raise S3PresignError(key, code, message) from exc
