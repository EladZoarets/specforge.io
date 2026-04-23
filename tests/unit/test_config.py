from unittest.mock import MagicMock

import pytest
from core.config import (
    _SSM_PARAM_MAP,
    PartialSSMConfig,
    Settings,
    load_settings,
    load_settings_from_ssm,
)
from services.ssm_service import SSMError

_ALL_VARS = {
    "ANTHROPIC_API_KEY": "test-key",
    "JIRA_BASE_URL": "https://test.atlassian.net",
    "JIRA_TOKEN": "test-token",
    "JIRA_USER_EMAIL": "test@example.com",
    "S3_BUCKET": "test-bucket",
    "WEBHOOK_SECRET": "test-secret",
    "QUALITY_THRESHOLD": "7.0",
}


def test_load_settings_all_present(monkeypatch):
    for k, v in _ALL_VARS.items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.anthropic_api_key == "test-key"
    assert s.jira_base_url == "https://test.atlassian.net"
    assert s.s3_bucket == "test-bucket"


def test_load_settings_one_missing(monkeypatch):
    for k, v in _ALL_VARS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        load_settings()


def test_load_settings_multiple_missing(monkeypatch):
    for k in _ALL_VARS:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(EnvironmentError) as exc_info:
        load_settings()
    msg = str(exc_info.value)
    for var in _ALL_VARS:
        assert var in msg, f"Expected {var!r} in error message"


# ---------------------------------------------------------------------------
# SSM loader
# ---------------------------------------------------------------------------


# Canned values keyed by the SSM parameter name (mirrors how a real SSM
# backend would answer). Field values deliberately differ from the env-var
# values above so we can prove the SSM loader isn't reading env as a fallback.
_SSM_VALUES: dict[str, str] = {
    "/specforge/anthropic_api_key": "ssm-anthropic-key",
    "/specforge/jira_url": "https://ssm.atlassian.net",
    "/specforge/jira_email": "ssm-user@example.com",
    "/specforge/jira_api_token": "ssm-jira-token",
    "/specforge/s3_bucket": "ssm-bucket",
    "/specforge/webhook_secret": "ssm-webhook-secret",
    "/specforge/quality_threshold": "7.5",
}


def _mock_ssm(values: dict[str, str] | None = None) -> MagicMock:
    """Build a MagicMock SSMService whose get_parameter_if_exists returns canned values.

    Returns ``None`` for unknown names (mirroring ParameterNotFound → None
    semantics) so a test can omit a key from ``values`` to simulate a
    missing parameter.
    """
    values = values if values is not None else _SSM_VALUES
    svc = MagicMock()
    svc.get_parameter_if_exists = MagicMock(side_effect=lambda name: values.get(name))
    return svc


def test_load_settings_from_ssm_happy_path():
    svc = _mock_ssm()
    s = load_settings_from_ssm(svc)

    assert isinstance(s, Settings)
    assert s.anthropic_api_key == "ssm-anthropic-key"
    assert s.jira_base_url == "https://ssm.atlassian.net"
    assert s.jira_user_email == "ssm-user@example.com"
    assert s.jira_token == "ssm-jira-token"
    assert s.s3_bucket == "ssm-bucket"
    assert s.webhook_secret == "ssm-webhook-secret"
    # Critical: numeric coercion.
    assert isinstance(s.quality_threshold, float)
    assert s.quality_threshold == 7.5


def test_load_settings_from_ssm_propagates_ssm_error():
    """If SSM itself is broken (network/permission/etc., not a missing
    parameter), :class:`SSMError` must propagate unchanged. The caller — not
    the loader — decides whether to fall back to env vars.
    """
    svc = MagicMock()
    svc.get_parameter_if_exists = MagicMock(
        side_effect=SSMError("Failed to get parameter '/specforge/s3_bucket': ...")
    )
    with pytest.raises(SSMError, match="s3_bucket"):
        load_settings_from_ssm(svc)


def test_load_settings_from_ssm_missing_param_raises_partial_ssm_config():
    """A missing SSM parameter (ParameterNotFound → None from the helper)
    means the operator only populated part of the config. That's NOT a
    reachability error — raise :class:`PartialSSMConfig` so the caller
    refuses env fallback and surfaces a hard 500.
    """
    bad = dict(_SSM_VALUES)
    bad.pop("/specforge/webhook_secret")  # simulate ParameterNotFound
    svc = _mock_ssm(bad)

    with pytest.raises(PartialSSMConfig, match="webhook_secret"):
        load_settings_from_ssm(svc)


def test_load_settings_from_ssm_invalid_quality_threshold():
    """A non-float threshold is operator-facing config error, not a
    reachability error — translate to :class:`PartialSSMConfig` so callers
    do NOT fall back to env vars silently.
    """
    bad = dict(_SSM_VALUES)
    bad["/specforge/quality_threshold"] = "not-a-number"
    svc = _mock_ssm(bad)

    with pytest.raises(PartialSSMConfig, match="quality_threshold") as exc_info:
        load_settings_from_ssm(svc)
    # Clear message that surfaces the offending raw value.
    assert "not-a-number" in str(exc_info.value)


def test_load_settings_from_ssm_strips_whitespace():
    """AWS console copy-paste commonly leaves a trailing newline. The
    loader must strip whitespace so HMAC/URL parsing doesn't silently fail
    downstream.
    """
    padded = {
        "/specforge/anthropic_api_key": "  ssm-anthropic-key\n",
        "/specforge/jira_url": "\thttps://ssm.atlassian.net\n",
        "/specforge/jira_email": "ssm-user@example.com\n",
        "/specforge/jira_api_token": "  ssm-jira-token  ",
        "/specforge/s3_bucket": "ssm-bucket\r\n",
        "/specforge/webhook_secret": "  webhook-secret-value\n",
        "/specforge/quality_threshold": "  7.5\n",
    }
    svc = _mock_ssm(padded)
    s = load_settings_from_ssm(svc)

    assert s.anthropic_api_key == "ssm-anthropic-key"
    assert s.jira_base_url == "https://ssm.atlassian.net"
    assert s.jira_user_email == "ssm-user@example.com"
    assert s.jira_token == "ssm-jira-token"
    assert s.s3_bucket == "ssm-bucket"
    assert s.webhook_secret == "webhook-secret-value"
    assert s.quality_threshold == 7.5


def test_load_settings_from_ssm_whitespace_only_raises_partial_ssm_config():
    """A parameter set to only-whitespace is operator error, not a
    silently-acceptable blank config.
    """
    bad = dict(_SSM_VALUES)
    bad["/specforge/webhook_secret"] = "   \n\t "
    svc = _mock_ssm(bad)

    with pytest.raises(PartialSSMConfig, match="webhook_secret"):
        load_settings_from_ssm(svc)


def test_load_settings_from_ssm_uses_correct_parameter_names():
    """Spot-check the name mapping: the three Jira parameters must use their
    SSM-side names (jira_url, jira_email, jira_api_token), not their Settings
    field names (jira_base_url, jira_user_email, jira_token).
    """
    svc = _mock_ssm()
    load_settings_from_ssm(svc)

    called = {c.args[0] for c in svc.get_parameter_if_exists.call_args_list}

    # The three Jira-related name remappings.
    assert "/specforge/jira_url" in called, "jira_base_url must map to /specforge/jira_url"
    assert "/specforge/jira_email" in called, "jira_user_email must map to /specforge/jira_email"
    assert (
        "/specforge/jira_api_token" in called
    ), "jira_token must map to /specforge/jira_api_token"

    # And the Settings-side field names must NOT appear as SSM names —
    # guards against a refactor that accidentally uses field names as keys.
    assert "/specforge/jira_base_url" not in called
    assert "/specforge/jira_user_email" not in called
    assert "/specforge/jira_token" not in called

    # All 7 mapped parameters were queried exactly once each.
    assert called == set(_SSM_PARAM_MAP.values())
    assert svc.get_parameter_if_exists.call_count == len(_SSM_PARAM_MAP)


def test_ssm_param_map_keys_match_settings_fields():
    """Defensive: if someone renames a Settings field, the mapping must be
    kept in lockstep. Fails loudly rather than silently returning wrong data.
    """
    import dataclasses

    settings_fields = {f.name for f in dataclasses.fields(Settings)}
    assert set(_SSM_PARAM_MAP.keys()) == settings_fields


_SECRET_FIELDS = ("anthropic_api_key", "jira_token", "webhook_secret")


def test_partial_ssm_config_errors_do_not_embed_secret_values():
    """PartialSSMConfig error messages must never include raw values from
    secret fields — guards against copy-paste of the threshold error pattern
    (which legitimately embeds the raw threshold string) being applied to
    credential-bearing fields, which would silently leak secrets to CloudWatch.

    The current code only includes the threshold raw value in errors; this
    test locks that invariant in so a future refactor can't accidentally
    start including API keys or tokens.
    """
    # Provide valid values for all fields except quality_threshold
    # (set to non-float to trigger PartialSSMConfig with an embedded value).
    # Use distinctive sentinels for secret fields so we can detect leakage.
    secret_sentinels = {
        "/specforge/anthropic_api_key": "sk-ant-SUPERSECRET-APIKEY",
        "/specforge/jira_api_token": "jira-VERYSECRET-token",
        "/specforge/webhook_secret": "webhook-CLASSIFIED-secret",
    }
    values = dict(_SSM_VALUES)
    for k, v in secret_sentinels.items():
        values[k] = v
    values["/specforge/quality_threshold"] = "not-a-float"

    svc = _mock_ssm(values)
    with pytest.raises(PartialSSMConfig) as exc_info:
        load_settings_from_ssm(svc)

    error_msg = str(exc_info.value)
    # The threshold raw value is acceptable in the error (not a secret).
    assert "not-a-float" in error_msg
    # Secret field values must NEVER appear in error messages.
    for sentinel in secret_sentinels.values():
        assert sentinel not in error_msg, (
            f"Secret value {sentinel!r} leaked into PartialSSMConfig error"
        )
