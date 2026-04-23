"""
Integration tests for handler.py SSM init and lazy re-init behaviour.

These tests verify the three init-state outcomes a warm invocation can hit:
  1. Transient SSMError on cold start → retry on warm invocation → succeeds.
  2. PartialSSMConfig on cold start → permanent hard-fail, no retry.
  3. SSMError on cold start AND on retry → still 500, _INIT_ERROR updated.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import handler
from core.config import PartialSSMConfig, Settings
from services.ssm_service import SSMError

_FAKE_SETTINGS = Settings(
    anthropic_api_key="key",
    jira_base_url="https://test.atlassian.net",
    jira_token="tok",
    jira_user_email="test@example.com",
    s3_bucket="test-bucket",
    webhook_secret="secret",
)

_EVENT: dict = {}
_CTX = None


def test_transient_ssm_error_recovers_on_warm_invocation(monkeypatch):
    """SSMError on cold start → warm invocation retries and succeeds → 200."""
    monkeypatch.setattr(handler, "_INIT_ERROR", SSMError("ThrottlingException"))
    monkeypatch.setattr(handler, "_settings", None)

    with patch("handler.load_settings_from_ssm", return_value=_FAKE_SETTINGS):
        response = handler.lambda_handler(_EVENT, _CTX)

    assert response["statusCode"] == 200
    assert handler._INIT_ERROR is None
    assert handler._settings is _FAKE_SETTINGS


def test_partial_ssm_config_is_permanent_hard_fail(monkeypatch):
    """PartialSSMConfig on cold start → every warm invocation returns 500, no retry."""
    original_error = PartialSSMConfig("Required SSM parameter not found: '/specforge/webhook_secret'")
    monkeypatch.setattr(handler, "_INIT_ERROR", original_error)
    monkeypatch.setattr(handler, "_settings", None)

    with patch("handler.load_settings_from_ssm") as mock_load:
        response = handler.lambda_handler(_EVENT, _CTX)
        mock_load.assert_not_called()

    assert response["statusCode"] == 500
    assert handler._INIT_ERROR is original_error


def test_transient_ssm_error_persists_returns_500(monkeypatch):
    """SSMError on cold start → warm invocation retries but SSM still fails → 500."""
    monkeypatch.setattr(handler, "_INIT_ERROR", SSMError("ThrottlingException"))
    monkeypatch.setattr(handler, "_settings", None)

    with patch("handler.load_settings_from_ssm", side_effect=SSMError("still throttled")):
        response = handler.lambda_handler(_EVENT, _CTX)

    assert response["statusCode"] == 500
    assert isinstance(handler._INIT_ERROR, SSMError)
