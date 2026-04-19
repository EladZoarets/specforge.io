"""
Webhook HMAC validation and body parsing for specforge.io.

WebhookAuthError  -> HTTP 401
WebhookParseError -> HTTP 400
"""
from __future__ import annotations

import hashlib
import hmac
import json

from pydantic import ValidationError

from .models import WebhookPayload

_MAX_BODY_BYTES = 1_048_576  # 1 MB — guard against payload DoS


class WebhookAuthError(Exception):
    http_status: int = 401


class WebhookParseError(Exception):
    http_status: int = 400


def validate_signature(
    payload_body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """Validate an HMAC-SHA256 webhook signature.

    Raises:
        WebhookAuthError: if the header is missing, malformed, or the digest
            does not match the expected HMAC of *payload_body*.
    """
    # Guard: missing or empty header
    if not signature_header:
        raise WebhookAuthError("Missing X-Hub-Signature-256 header")
    # Parse prefix
    parts = signature_header.split("=", 1)
    if len(parts) != 2 or parts[0] != "sha256" or not parts[1]:
        raise WebhookAuthError("Malformed signature header")
    # Normalise to lowercase so callers emitting uppercase hex (e.g. Bitbucket,
    # custom proxies) are not silently rejected.
    provided_hex = parts[1].lower()
    # Compute and compare (timing-safe)
    expected_hex = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hex, provided_hex):
        raise WebhookAuthError("Signature mismatch")


def parse_webhook_body(body: str | bytes) -> WebhookPayload:
    """Decode, JSON-parse, and validate *body* as a :class:`WebhookPayload`.

    Raises:
        WebhookParseError: if *body* is not valid UTF-8, not valid JSON, or
            does not satisfy the :class:`WebhookPayload` schema.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    if len(body) > _MAX_BODY_BYTES:
        raise WebhookParseError(f"Body exceeds maximum size of {_MAX_BODY_BYTES} bytes")
    try:
        body = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebhookParseError(f"Body is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookParseError(f"Invalid JSON: {exc}") from exc
    try:
        return WebhookPayload.model_validate(data)
    except ValidationError as exc:
        raise WebhookParseError(f"Missing required fields: {exc}") from exc
