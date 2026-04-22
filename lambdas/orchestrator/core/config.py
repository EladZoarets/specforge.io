from __future__ import annotations

import dataclasses
import os

from services.ssm_service import SSMService


class PartialSSMConfig(Exception):
    """Raised when SSM is reachable but returns incomplete/invalid config.

    This is a hard error — callers should NOT fall back to env vars on this,
    because SSM is the intended source of truth and partial config means an
    operator made a mistake that deserves a loud 500 rather than silent
    stale-env-var substitution.
    """


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

    Error semantics (important — callers branch on exception type):

    * :class:`PartialSSMConfig` — SSM is reachable but the configuration is
      incomplete or malformed (a parameter is missing, blank, or the
      quality_threshold isn't parseable as a float). This is an operator-
      facing error; the caller MUST NOT fall back to env vars, because
      silent stale-env substitution would mask a real bug.
    * :class:`~services.ssm_service.SSMError` — the underlying SSM service
      is unreachable / denied / otherwise broken. The caller may choose to
      fall back to env vars (used by local dev / pytest where no IAM role
      is present).

    The two exception types are deliberately non-overlapping so handler
    init can branch cleanly.
    """
    values: dict[str, str] = {}
    for field, param_name in _SSM_PARAM_MAP.items():
        # ``get_parameter_if_exists`` returns ``None`` on ParameterNotFound
        # and re-raises :class:`SSMError` on everything else (network /
        # permission / client error). That's the split we want: "missing"
        # → PartialSSMConfig (operator mistake), "broken" → SSMError
        # (environment issue).
        raw = ssm_service.get_parameter_if_exists(param_name)
        if raw is None:
            raise PartialSSMConfig(f"missing SSM parameter: {param_name}")
        # Strip whitespace (copy-paste from the AWS console commonly leaves
        # a trailing newline, which silently breaks HMAC and URL parsing).
        stripped = raw.strip()
        if not stripped:
            raise PartialSSMConfig(
                f"SSM parameter {param_name!r} is empty after strip"
            )
        values[field] = stripped

    threshold_raw = values["quality_threshold"]
    try:
        quality_threshold = float(threshold_raw)
    except ValueError as exc:
        # Bad float is operator config error, not a reachability error —
        # translate to PartialSSMConfig so the handler doesn't fall back to
        # env vars silently.
        raise PartialSSMConfig(
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
