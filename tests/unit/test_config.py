import pytest

from core.config import Settings, load_settings

_ALL_VARS = {
    "ANTHROPIC_API_KEY": "test-key",
    "JIRA_BASE_URL": "https://test.atlassian.net",
    "JIRA_TOKEN": "test-token",
    "JIRA_USER_EMAIL": "test@example.com",
    "S3_BUCKET": "test-bucket",
    "WEBHOOK_SECRET": "test-secret",
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
