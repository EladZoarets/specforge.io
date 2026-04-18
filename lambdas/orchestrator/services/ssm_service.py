from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError


class SSMError(Exception):
    pass


class SSMService:
    def __init__(self, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            import boto3  # noqa: PLC0415 — lazy import to avoid cold-start overhead
            self._client = boto3.client("ssm")

    def get_parameter(self, name: str, *, with_decryption: bool = True) -> str:
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=with_decryption)
            return response["Parameter"]["Value"]
        except ClientError as exc:
            raise SSMError(f"Failed to get parameter {name!r}: {exc}") from exc

    def put_parameter(self, name: str, value: str, *, overwrite: bool = False) -> None:
        try:
            self._client.put_parameter(
                Name=name,
                Value=value,
                Type="SecureString",
                Overwrite=overwrite,
            )
        except ClientError as exc:
            raise SSMError(f"Failed to put parameter {name!r}: {exc}") from exc
