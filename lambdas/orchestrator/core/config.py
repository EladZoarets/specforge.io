from __future__ import annotations

import dataclasses
import os

from services.ssm_service import SSMService

_REQUIRED_VARS = (
    "ANTHROPIC_API_KEY",
    "JIRA_BASE_URL",
    "JIRA_TOKEN",
    "JIRA_USER_EMAIL",
    "S3_BUCKET",
    "WEBHOOK_SECRET",
    "QUALITY_THRESHOLD",
)

# Maps ``Settings`` field name → SSM parameter name under ``/specforge/``.
# Names deliberately do NOT match the field names 1:1 — they follow the
# convention used by ``bootstrap-ssm`` and the IAM policy (``/specforge/*``).
# Referenced by ``load_settings_from_ssm`` and by any future code that needs
# to know the canonical parameter names (e.g. diagnostics tooling).
_SSM_PARAM_MAP: dict[str, str] = {
    "anthropic_api_key": "/specforge/anthropic_api_key",
    "jira_base_url": "/specforge/jira_url",
    "jira_user_email": "/specforge/jira_email",
    "jira_token": "/specforge/jira_api_token",
    "s3_bucket": "/specforge/s3_bucket",
    "webhook_secret": "/specforge/webhook_secret",
    "quality_threshold": "/specforge/quality_threshold",
}


@dataclasses.dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    jira_base_url: str
    jira_token: str
    jira_user_email: str
    s3_bucket: str
    webhook_secret: str
    quality_threshold: float


def load_settings() -> Settings:
    missing = [var for var in _REQUIRED_VARS if not os.environ.get(var)]
    if missing:
        raise OSError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    try:
        quality_threshold = float(os.environ["QUALITY_THRESHOLD"])
    except ValueError as exc:
        raise OSError(
            f"QUALITY_THRESHOLD must be a float, got {os.environ['QUALITY_THRESHOLD']!r}"
        ) from exc
    return Settings(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        jira_base_url=os.environ["JIRA_BASE_URL"],
        jira_token=os.environ["JIRA_TOKEN"],
        jira_user_email=os.environ["JIRA_USER_EMAIL"],
        s3_bucket=os.environ["S3_BUCKET"],
        webhook_secret=os.environ["WEBHOOK_SECRET"],
        quality_threshold=quality_threshold,
    )


def load_settings_from_ssm(ssm_service: SSMService) -> Settings:
    """Load Settings from SSM parameters under /specforge/.

    Fetches each required parameter via the given SSMService.
    Raises SSMError on any fetch failure. Raises ValueError if
    QUALITY_THRESHOLD is not a valid float.
    """
    values: dict[str, str] = {
        field: ssm_service.get_parameter(param_name)
        for field, param_name in _SSM_PARAM_MAP.items()
    }
    threshold_raw = values["quality_threshold"]
    try:
        quality_threshold = float(threshold_raw)
    except ValueError as exc:
        raise ValueError(
            f"SSM parameter {_SSM_PARAM_MAP['quality_threshold']!r} must be a "
            f"float, got {threshold_raw!r}"
        ) from exc
    return Settings(
        anthropic_api_key=values["anthropic_api_key"],
        jira_base_url=values["jira_base_url"],
        jira_token=values["jira_token"],
        jira_user_email=values["jira_user_email"],
        s3_bucket=values["s3_bucket"],
        webhook_secret=values["webhook_secret"],
        quality_threshold=quality_threshold,
    )
