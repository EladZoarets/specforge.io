"""
Unit tests for lambdas/orchestrator/core/webhook.py.

Covers validate_signature (7 cases), parse_webhook_body (7 cases),
and exception http_status attributes (2 cases) — 16 tests total.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from core.models import WebhookPayload
from core.webhook import (
    MAX_BODY_BYTES,
    WebhookAuthError,
    WebhookParseError,
    parse_webhook_body,
    validate_signature,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

SECRET = "test-secret-key"

VALID_BODY = json.dumps(
    {
        "issue_key": "PROJ-1",
        "issue_summary": "S",
        "issue_description": "D",
        "project_key": "PROJ",
    }
).encode()


def _sig(body: bytes) -> str:
    """Return a correctly-formed sha256= HMAC signature for *body*."""
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# validate_signature tests
# ---------------------------------------------------------------------------


def test_valid_signature_passes() -> None:
    """A correct sha256= header must not raise."""
    validate_signature(VALID_BODY, _sig(VALID_BODY), SECRET)  # no exception


def test_missing_header_raises() -> None:
    """signature_header=None must raise WebhookAuthError."""
    with pytest.raises(WebhookAuthError):
        validate_signature(VALID_BODY, None, SECRET)


def test_empty_header_raises() -> None:
    """An empty string signature header must raise WebhookAuthError."""
    with pytest.raises(WebhookAuthError):
        validate_signature(VALID_BODY, "", SECRET)


def test_wrong_digest_raises() -> None:
    """A sha256= header with incorrect hex must raise WebhookAuthError."""
    bad_sig = "sha256=" + "a" * 64
    with pytest.raises(WebhookAuthError):
        validate_signature(VALID_BODY, bad_sig, SECRET)


def test_malformed_prefix_raises() -> None:
    """A header using a non-sha256 algorithm prefix must raise WebhookAuthError."""
    md5_sig = "md5=" + hmac.new(SECRET.encode(), VALID_BODY, hashlib.md5).hexdigest()
    with pytest.raises(WebhookAuthError):
        validate_signature(VALID_BODY, md5_sig, SECRET)


def test_no_equals_raises() -> None:
    """A header with no '=' separator must raise WebhookAuthError."""
    with pytest.raises(WebhookAuthError):
        validate_signature(VALID_BODY, "justgarbage", SECRET)


def test_tampered_body_raises() -> None:
    """Signature computed on original body must fail when a different body is passed."""
    original_sig = _sig(VALID_BODY)
    tampered_body = VALID_BODY + b" tampered"
    with pytest.raises(WebhookAuthError):
        validate_signature(tampered_body, original_sig, SECRET)


# ---------------------------------------------------------------------------
# parse_webhook_body tests
# ---------------------------------------------------------------------------


def test_valid_bytes_returns_payload() -> None:
    """Bytes containing all required fields must return a WebhookPayload."""
    result = parse_webhook_body(VALID_BODY)
    assert isinstance(result, WebhookPayload)
    assert result.issue_key == "PROJ-1"
    assert result.project_key == "PROJ"


def test_valid_str_returns_payload() -> None:
    """A str containing all required fields must return a WebhookPayload."""
    result = parse_webhook_body(VALID_BODY.decode("utf-8"))
    assert isinstance(result, WebhookPayload)
    assert result.issue_summary == "S"


def test_invalid_json_raises() -> None:
    """Non-JSON bytes must raise WebhookParseError."""
    with pytest.raises(WebhookParseError):
        parse_webhook_body(b"not json")


def test_missing_field_raises() -> None:
    """JSON missing required field project_key must raise WebhookParseError."""
    body = json.dumps(
        {"issue_key": "PROJ-1", "issue_summary": "S", "issue_description": "D"}
    ).encode()
    with pytest.raises(WebhookParseError):
        parse_webhook_body(body)


def test_empty_body_raises() -> None:
    """An empty body must raise WebhookParseError."""
    with pytest.raises(WebhookParseError):
        parse_webhook_body(b"")


def test_invalid_utf8_raises() -> None:
    """Bytes that are not valid UTF-8 must raise WebhookParseError."""
    with pytest.raises(WebhookParseError):
        parse_webhook_body(b"\xff\xfe")


def test_extra_fields_ignored() -> None:
    """JSON with extra unknown keys must still return a valid WebhookPayload."""
    body = json.dumps(
        {
            "issue_key": "PROJ-1",
            "issue_summary": "S",
            "issue_description": "D",
            "project_key": "PROJ",
            "unexpected_field": "should be ignored",
        }
    ).encode()
    result = parse_webhook_body(body)
    assert isinstance(result, WebhookPayload)
    assert result.issue_key == "PROJ-1"


# ---------------------------------------------------------------------------
# Exception http_status attribute tests
# ---------------------------------------------------------------------------


def test_webhook_auth_error_http_status() -> None:
    """WebhookAuthError must carry http_status == 401."""
    assert WebhookAuthError.http_status == 401


def test_webhook_parse_error_http_status() -> None:
    """WebhookParseError must carry http_status == 400."""
    assert WebhookParseError.http_status == 400


# ---------------------------------------------------------------------------
# Fixes: uppercase hex and payload size guard
# ---------------------------------------------------------------------------


def test_uppercase_hex_signature_passes() -> None:
    """Uppercase hex in sha256= header must be accepted (normalised to lowercase)."""
    hex_part = hmac.new(SECRET.encode(), VALID_BODY, hashlib.sha256).hexdigest().upper()
    validate_signature(VALID_BODY, f"sha256={hex_part}", SECRET)  # must not raise


def test_body_too_large_bytes_raises() -> None:
    """A bytes body larger than MAX_BODY_BYTES must raise WebhookParseError."""
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(WebhookParseError, match="exceeds maximum size"):
        parse_webhook_body(oversized)


def test_body_too_large_str_raises() -> None:
    """A str body larger than MAX_BODY_BYTES must also raise WebhookParseError."""
    oversized = "x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(WebhookParseError, match="exceeds maximum size"):
        parse_webhook_body(oversized)
