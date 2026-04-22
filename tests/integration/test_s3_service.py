from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from services.s3_service import S3Service, S3UploadError


def _fixed_clock() -> datetime:
    return datetime(2026, 4, 22, tzinfo=UTC)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "PutObject",
    )


def test_upload_spec_places_object_at_expected_key(s3_client):
    svc = S3Service("test-specforge-bucket", client=s3_client, clock=_fixed_clock)

    key = svc.upload_spec("SPEC-1", "# hi")

    assert key == "specs/SPEC-1/2026-04-22/SPEC.md"
    obj = s3_client.get_object(Bucket="test-specforge-bucket", Key=key)
    assert obj["ContentType"] == "text/markdown"
    assert obj["Body"].read().decode() == "# hi"


def test_upload_spec_uses_utc_date_from_clock(s3_client):
    # A timezone-aware datetime in a different zone should still be formatted from the
    # clock output directly — we inject a UTC clock and trust the caller.
    svc = S3Service(
        "test-specforge-bucket",
        client=s3_client,
        clock=lambda: datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    key = svc.upload_spec("STORY-42", "content")

    assert key == "specs/STORY-42/2025-01-02/SPEC.md"


def test_presigned_url_includes_bucket_and_key(s3_client):
    svc = S3Service("test-specforge-bucket", client=s3_client)

    url = svc.generate_presigned_url("specs/SPEC-1/2026-04-22/SPEC.md")

    assert isinstance(url, str)
    assert "test-specforge-bucket" in url
    assert "SPEC.md" in url


def test_retries_transient_then_succeeds():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = [
        _client_error("Throttling"),
        _client_error("ServiceUnavailable"),
        {"ResponseMetadata": {"HTTPStatusCode": 200}},
    ]
    sleeps: list[float] = []
    svc = S3Service(
        "test-specforge-bucket",
        client=mock_client,
        clock=_fixed_clock,
        sleep=sleeps.append,
    )

    key = svc.upload_spec("SPEC-1", "# hi")

    assert key == "specs/SPEC-1/2026-04-22/SPEC.md"
    assert mock_client.put_object.call_count == 3
    assert sleeps == [0.1, 0.2]


def test_wraps_persistent_client_error_as_s3_upload_error():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = _client_error("Throttling", "still throttled")
    sleeps: list[float] = []
    svc = S3Service(
        "test-specforge-bucket",
        client=mock_client,
        clock=_fixed_clock,
        sleep=sleeps.append,
    )

    with pytest.raises(S3UploadError) as excinfo:
        svc.upload_spec("SPEC-1", "# hi")

    assert mock_client.put_object.call_count == 3
    assert sleeps == [0.1, 0.2]
    assert excinfo.value.bucket == "test-specforge-bucket"
    assert excinfo.value.key == "specs/SPEC-1/2026-04-22/SPEC.md"
    assert excinfo.value.code == "Throttling"


def test_no_retry_on_no_such_bucket_is_wrapped_immediately():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = _client_error(
        "NoSuchBucket", "The specified bucket does not exist"
    )
    sleeps: list[float] = []
    svc = S3Service(
        "test-specforge-bucket",
        client=mock_client,
        clock=_fixed_clock,
        sleep=sleeps.append,
    )

    with pytest.raises(S3UploadError) as excinfo:
        svc.upload_spec("SPEC-1", "# hi")

    assert mock_client.put_object.call_count == 1
    assert sleeps == []
    assert excinfo.value.code == "NoSuchBucket"


def test_no_retry_on_access_denied_is_wrapped_immediately():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = _client_error("AccessDenied", "denied")
    svc = S3Service(
        "test-specforge-bucket",
        client=mock_client,
        clock=_fixed_clock,
        sleep=lambda _s: None,
    )

    with pytest.raises(S3UploadError) as excinfo:
        svc.upload_spec("SPEC-1", "# hi")

    assert mock_client.put_object.call_count == 1
    assert excinfo.value.code == "AccessDenied"
