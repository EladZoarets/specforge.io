from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from services.s3_service import S3PresignError, S3Service, S3UploadError


def _fixed_clock() -> datetime:
    return datetime(2026, 4, 22, tzinfo=UTC)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "PutObject",
    )


def _endpoint_error() -> EndpointConnectionError:
    return EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com")


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


# --- story_id validation (Finding 1) ----------------------------------------

def test_upload_spec_accepts_standard_story_id(s3_client):
    svc = S3Service("test-specforge-bucket", client=s3_client, clock=_fixed_clock)

    key = svc.upload_spec("SPEC-1", "# hi")

    assert key == "specs/SPEC-1/2026-04-22/SPEC.md"


@pytest.mark.parametrize(
    "bad_id",
    [
        "foo/bar",
        "..",
        "",
        "SPÉC-1",
        "SPEC",
        "-1",
        "SPEC-",
        "SPEC 1",
        "specs/../evil-1",
    ],
)
def test_upload_spec_rejects_invalid_story_id(bad_id):
    mock_client = MagicMock()
    svc = S3Service(
        "test-specforge-bucket",
        client=mock_client,
        clock=_fixed_clock,
        sleep=lambda _s: None,
    )

    with pytest.raises(ValueError, match="Invalid story_id"):
        svc.upload_spec(bad_id, "# hi")

    # Validation must happen BEFORE any S3 call is issued.
    assert mock_client.put_object.call_count == 0


# --- BotoCoreError retry (Finding 2) ----------------------------------------

def test_retries_botocore_network_error_then_succeeds():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = [
        _endpoint_error(),
        _endpoint_error(),
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


def test_persistent_botocore_network_error_wrapped_after_retries():
    mock_client = MagicMock()
    mock_client.put_object.side_effect = _endpoint_error()
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
    assert excinfo.value.code == "EndpointConnectionError"


# --- Presign error wrapping (Finding 3) -------------------------------------

def test_presigned_url_wraps_client_error_as_s3_presign_error():
    mock_client = MagicMock()
    mock_client.generate_presigned_url.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetObject",
    )
    svc = S3Service("test-specforge-bucket", client=mock_client)

    with pytest.raises(S3PresignError) as excinfo:
        svc.generate_presigned_url("specs/SPEC-1/2026-04-22/SPEC.md")

    assert excinfo.value.key == "specs/SPEC-1/2026-04-22/SPEC.md"
    assert excinfo.value.code == "AccessDenied"
    # Sibling of S3UploadError, not a parent — neither should be a subclass of the other.
    assert not isinstance(excinfo.value, S3UploadError)
