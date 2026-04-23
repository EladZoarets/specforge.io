from __future__ import annotations

import json
import logging
from typing import Any

from core.config import PartialSSMConfig, Settings, load_settings_from_ssm
from services.ssm_service import SSMError, SSMService

logger = logging.getLogger(__name__)

_settings: Settings | None = None
_INIT_ERROR: Exception | None = None


def _init(ssm_service: SSMService | None = None) -> None:
    global _settings, _INIT_ERROR
    try:
        _settings = load_settings_from_ssm(ssm_service or SSMService())
        _INIT_ERROR = None
    except Exception as exc:
        _INIT_ERROR = exc
        _settings = None


_init()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _settings, _INIT_ERROR

    if _INIT_ERROR is not None:
        if isinstance(_INIT_ERROR, SSMError):
            logger.warning("Retrying SSM init after transient error: %s", _INIT_ERROR)
            _init()
        if _INIT_ERROR is not None:
            logger.error("Handler init failed: %s", _INIT_ERROR)
            return {"statusCode": 500, "body": json.dumps({"error": "Service unavailable"})}

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
