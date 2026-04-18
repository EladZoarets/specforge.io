from __future__ import annotations

import dataclasses
import os

_REQUIRED_VARS = (
    "ANTHROPIC_API_KEY",
    "JIRA_BASE_URL",
    "JIRA_TOKEN",
    "JIRA_USER_EMAIL",
    "S3_BUCKET",
    "WEBHOOK_SECRET",
)


@dataclasses.dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    jira_base_url: str
    jira_token: str
    jira_user_email: str
    s3_bucket: str
    webhook_secret: str


def load_settings() -> Settings:
    missing = [var for var in _REQUIRED_VARS if not os.environ.get(var)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return Settings(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        jira_base_url=os.environ["JIRA_BASE_URL"],
        jira_token=os.environ["JIRA_TOKEN"],
        jira_user_email=os.environ["JIRA_USER_EMAIL"],
        s3_bucket=os.environ["S3_BUCKET"],
        webhook_secret=os.environ["WEBHOOK_SECRET"],
    )
