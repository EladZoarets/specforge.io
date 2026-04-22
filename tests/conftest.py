import os
import pytest
import boto3
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    """Mock AWS credentials for all tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def base_env(monkeypatch):
    """Set all required environment variables for Settings."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("JIRA_BASE_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_TOKEN", "test-jira-token")
    monkeypatch.setenv("JIRA_USER_EMAIL", "test@example.com")
    monkeypatch.setenv("S3_BUCKET", "test-specforge-bucket")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("QUALITY_THRESHOLD", "7.0")


@pytest.fixture
def ssm_client():
    """Moto-backed SSM client."""
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def s3_client():
    """Moto-backed S3 client."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-specforge-bucket")
        yield client
