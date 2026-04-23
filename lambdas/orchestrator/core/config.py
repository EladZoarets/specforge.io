from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.ssm_service import SSMService

from services.ssm_service import SSMError  # noqa: E402 — after TYPE_CHECKING block

_REQUIRED_VARS = (
    "ANTHROPIC_API_KEY",
    "JIRA_BASE_URL",
    "JIRA_TOKEN",
    "JIRA_USER_EMAIL",
    "S3_BUCKET",
    "WEBHOOK_SECRET",
)

_SSM_PARAM_MAP: dict[str, str] = {
    "anthropic_api_key": "/specforge/anthropic_api_key",
    "jira_base_url": "/specforge/jira_url",
    "jira_token": "/specforge/jira_token",
    "jira_user_email": "/specforge/jira_user_email",
    "s3_bucket": "/specforge/s3_bucket",
    "webhook_secret": "/specforge/webhook_secret",
}


class PartialSSMConfig(Exception):
    """Required SSM parameters are absent or malformed — operator error, not transient."""


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


def load_settings_from_ssm(ssm: SSMService) -> Settings:
    values: dict[str, str] = {}
    for field, param_name in _SSM_PARAM_MAP.items():
        try:
            values[field] = ssm.get_parameter(param_name)
        except SSMError as exc:
            cause = exc.__cause__
            code = (
                cause.response.get("Error", {}).get("Code", "")
                if cause is not None and hasattr(cause, "response")
                else ""
            )
            if code == "ParameterNotFound":
                raise PartialSSMConfig(
                    f"Required SSM parameter not found: {param_name!r}"
                ) from exc
            raise
    return Settings(**values)
