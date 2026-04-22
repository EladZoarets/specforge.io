"""
AWS Lambda entry point for the specforge.io spec-generation pipeline.

Receives an API Gateway v2 HTTP event (Jira webhook), validates the HMAC
signature, parses the payload, runs Phase 1 → gate → Phase 2 → writer →
S3 upload → Jira comment/attachment, and returns a JSON response.

Cold-start pattern:
  - ``Settings`` and ``S3Service`` (stateless; boto3 client is thread-safe
    and reusable) are constructed at module import time and reused across
    warm invocations — this saves ~150 ms per warm call.
  - ``JiraService``, ``httpx.AsyncClient`` and ``anthropic.AsyncAnthropic``
    are constructed lazily inside ``_run_pipeline`` (per-invocation) because
    they bind to ``asyncio.get_running_loop()`` at construction; hoisting
    them to module scope would wedge warm invocations on a closed loop.
  - The Anthropic client is managed via ``async with`` so its underlying
    httpx client is closed on every path, avoiding FD leaks across warm
    invocations.
  - If module init raises, we capture the exception in ``_INIT_ERROR`` and
    serve 500s from every invocation rather than crashing the function.

HTTP contract:
  - 401 WebhookAuthError — signature missing / malformed / mismatched
  - 400 WebhookParseError — body not UTF-8 / not JSON / schema violation
  - 200 Pipeline success (both gate-pass and gate-fail paths)
  - 500 Any uncaught exception (pipeline / Jira / S3 / init)
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any

from agents.phase1.ambiguity_agent import AmbiguityAgent
from agents.phase1.complexity_agent import ComplexityAgent
from agents.phase1.quality_agent import QualityAgent
from agents.phase2.api_agent import ApiAgent
from agents.phase2.architecture_agent import ArchitectureAgent
from agents.phase2.edge_cases_agent import EdgeCasesAgent
from agents.phase2.testing_agent import TestingAgent
from core.config import Settings, load_settings, load_settings_from_ssm
from core.models import JiraStory, Phase1Result, WebhookPayload
from core.webhook import (
    MAX_BODY_BYTES,
    WebhookAuthError,
    WebhookParseError,
    parse_webhook_body,
    validate_signature,
)
from pipeline.phase1 import Phase1PipelineError, run_phase1
from pipeline.phase2 import Phase2PipelineError, run_phase2
from pipeline.writer import assemble_spec
from services.jira_service import JiraAPIError, JiraService
from services.s3_service import S3PresignError, S3Service, S3UploadError
from services.ssm_service import SSMService

logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Populated at module import; re-used by every warm invocation.
_SETTINGS: Settings | None = None
# S3Service has no event-loop state — hoist the boto3 client construction
# (~150 ms on cold start) out of the per-invocation path. JiraService, by
# contrast, owns the httpx.AsyncClient and stays per-invocation.
_S3_SERVICE: S3Service | None = None
# SSMService is held at module scope for testability even though the handler
# doesn't use it after init. A reference here lets tests reload the module
# and inspect how settings were sourced.
_SSM_SERVICE: SSMService | None = None
_INIT_ERROR: BaseException | None = None

# Preferred path: load settings from SSM (production Lambda, where the IAM
# role grants ssm:GetParameter on /specforge/* and no env vars are injected).
# Fallback path: if SSMService init or fetch fails — e.g. in local dev and
# pytest where boto3 has no credentials or the parameters aren't populated —
# fall back to env-var loading. The env fallback is what keeps the existing
# ``base_env`` fixture (and local ``make run``) working without changes.
try:
    _SSM_SERVICE = SSMService()
    _SETTINGS = load_settings_from_ssm(_SSM_SERVICE)
except BaseException as _ssm_exc:  # noqa: BLE001 — broad: SSM can fail many ways
    logger.info(
        "SSM settings load failed (%s); falling back to env vars", _ssm_exc
    )
    try:
        _SETTINGS = load_settings()
    except BaseException as _exc:  # noqa: BLE001 — capture *everything* so the
        # Lambda doesn't hard-crash on import; handler surfaces it as 500.
        _INIT_ERROR = _exc
        logger.exception("Module-level initialization failed: %s", _exc)
    else:
        _S3_SERVICE = S3Service(_SETTINGS.s3_bucket)
else:
    _S3_SERVICE = S3Service(_SETTINGS.s3_bucket)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "body": json.dumps(body)}


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _extract_body(event: dict[str, Any]) -> bytes:
    """Return the request body as raw bytes, honoring ``isBase64Encoded``.

    Size-gates the *raw* input before any decoding so a huge base64 payload
    can't OOM the Lambda via ``base64.b64decode`` before HMAC/parse run.
    Raises ``WebhookParseError`` (→ HTTP 400) if the raw input exceeds the
    cap or — for base64 input — if it isn't valid base64. Base64 inflates
    ~33%, so the b64 cap is ``MAX_BODY_BYTES * 2`` (conservative); raw
    bytes/str pass through at exactly ``MAX_BODY_BYTES``.
    """
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        # Cap the *source* string before decoding: b64 expansion is ~4/3, so
        # 2x is a safe upper bound that still admits any valid payload
        # under the eventual MAX_BODY_BYTES decoded limit.
        if len(raw) > MAX_BODY_BYTES * 2:
            raise WebhookParseError("Body exceeds maximum size")
        try:
            return base64.b64decode(raw)
        except (binascii.Error, ValueError) as exc:
            # Garbage base64 from the client is a 400, not a 500.
            raise WebhookParseError("Body is not valid base64") from exc
    if isinstance(raw, bytes):
        if len(raw) > MAX_BODY_BYTES:
            raise WebhookParseError("Body exceeds maximum size")
        return raw
    if len(raw) > MAX_BODY_BYTES:
        raise WebhookParseError("Body exceeds maximum size")
    return raw.encode("utf-8")


def _extract_signature(event: dict[str, Any]) -> str | None:
    """Look up the signature header, case-insensitively.

    API Gateway v2 lowercases headers, but we accept both forms so direct
    invocation (tests, local replays) isn't brittle.
    """
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "x-hub-signature-256":
            return value
    return None


def _payload_to_story(payload: WebhookPayload) -> JiraStory:
    """Map a Jira webhook payload into a ``JiraStory``.

    The webhook doesn't carry AC or story points; Phase 1 agents score
    accordingly (missing AC will reduce the INVEST score).
    """
    return JiraStory(
        id=payload.issue_key,
        title=payload.issue_summary,
        description=payload.issue_description,
        acceptance_criteria=[],
        story_points=None,
    )


# ---------------------------------------------------------------------------
# Comment bodies
# ---------------------------------------------------------------------------


def _gate_fail_comment(phase1: Phase1Result) -> str:
    """Short plain-text comment for gate-fail path.

    Includes composite + per-agent scores and the top 2-3 suggestions
    aggregated across agents (dedup, order-preserving).
    """
    suggestions: list[str] = []
    seen: set[str] = set()
    for agent in (phase1.quality, phase1.ambiguity, phase1.complexity):
        for s in agent.suggestions:
            if s not in seen:
                seen.add(s)
                suggestions.append(s)
            if len(suggestions) == 3:
                break
        if len(suggestions) == 3:
            break

    lines = [
        "Spec generation skipped: the story did not pass the Phase 1 quality gate.",
        "",
        f"Composite score: {phase1.composite_score:.2f}",
        f"- Quality:    {phase1.quality.score:.2f}",
        f"- Ambiguity:  {phase1.ambiguity.score:.2f}",
        f"- Complexity: {phase1.complexity.score:.2f}",
    ]
    if suggestions:
        lines.append("")
        lines.append("Top suggestions:")
        for s in suggestions:
            lines.append(f"- {s}")
    return "\n".join(lines)


def _gate_pass_comment(issue_key: str, presigned_url: str) -> str:
    return (
        f"Spec generated for {issue_key}. "
        f"View: {presigned_url} (expires in 1 hour)"
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(settings: Settings, payload: WebhookPayload) -> dict[str, Any]:
    """Drive the async pipeline end-to-end and return a response dict.

    Creates the per-invocation async resources (Anthropic client, httpx
    client) inside this coroutine so they bind to the running event loop.
    The Anthropic client is managed via ``async with`` so its underlying
    httpx client is closed on every path, including exceptions — warm
    invocations otherwise leak file descriptors.
    """
    # Lazy imports — the Anthropic SDK instantiates an httpx client in its
    # constructor, and we don't want to pay that cost (or event-loop bind)
    # at module import.
    import httpx  # noqa: PLC0415
    from anthropic import AsyncAnthropic  # noqa: PLC0415

    story = _payload_to_story(payload)

    # Module-level S3 service (init checks in ``lambda_handler`` guarantee
    # this is non-None by the time we get here).
    s3 = _S3_SERVICE
    assert s3 is not None  # noqa: S101 — invariant enforced by handler entry

    async with AsyncAnthropic(api_key=settings.anthropic_api_key) as anthropic_client:
        phase1_agents = {
            "quality": QualityAgent(anthropic_client),
            "ambiguity": AmbiguityAgent(anthropic_client),
            "complexity": ComplexityAgent(anthropic_client),
        }

        phase1 = await run_phase1(story, phase1_agents, settings.quality_threshold)

        async with httpx.AsyncClient() as http_client:
            jira = JiraService(
                settings.jira_base_url,
                settings.jira_user_email,
                settings.jira_token,
                client=http_client,
            )

            # Gate-fail path: post feedback comment and return without
            # touching S3.
            if not phase1.passed_gate:
                await jira.post_comment(
                    payload.issue_key, _gate_fail_comment(phase1)
                )
                logger.info(
                    "Gate-fail path complete for %s (composite=%.2f)",
                    payload.issue_key,
                    phase1.composite_score,
                )
                return _response(
                    200,
                    {
                        "message": "gate_failed",
                        "issue_key": payload.issue_key,
                        "composite_score": phase1.composite_score,
                    },
                )

            # Gate-pass path: Phase 2 → writer → S3 → Jira comment +
            # attachment.
            phase2_agents = {
                "architecture": ArchitectureAgent(anthropic_client),
                "api": ApiAgent(anthropic_client),
                "edge_cases": EdgeCasesAgent(anthropic_client),
                "testing": TestingAgent(anthropic_client),
            }
            phase2 = await run_phase2(
                story, phase1, phase2_agents, settings.quality_threshold
            )

            spec_markdown = assemble_spec(story, phase1, phase2)

            # S3 upload is sync (boto3); run in the default executor so we
            # don't block the event loop on the PUT.
            loop = asyncio.get_running_loop()
            s3_key = await loop.run_in_executor(
                None, s3.upload_spec, story.id, spec_markdown
            )
            presigned_url = await loop.run_in_executor(
                None, s3.generate_presigned_url, s3_key
            )

            await jira.post_comment(
                payload.issue_key,
                _gate_pass_comment(payload.issue_key, presigned_url),
            )
            await jira.attach_file(
                payload.issue_key,
                spec_markdown.encode("utf-8"),
                f"{story.id}-SPEC.md",
            )

    logger.info(
        "Gate-pass path complete for %s (s3_key=%s)", payload.issue_key, s3_key
    )
    return _response(
        200,
        {
            "message": "spec_generated",
            "issue_key": payload.issue_key,
            "s3_key": s3_key,
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    """API Gateway v2 entry point. Always returns a ``{statusCode, body}`` dict."""
    if _INIT_ERROR is not None or _SETTINGS is None or _S3_SERVICE is None:
        logger.error("Refusing invocation; module init failed: %s", _INIT_ERROR)
        return _response(500, {"error": "server_error"})

    try:
        # 0. Extract body, size-gated before any decode (400 on overflow)
        try:
            body_bytes = _extract_body(event)
        except WebhookParseError as exc:
            # Body too large — short-circuit before HMAC/parse run.
            logger.info("Webhook body rejected at extract: %s", exc)
            return _response(400, {"error": "bad_request", "detail": str(exc)})
        signature = _extract_signature(event)

        # 1. Validate signature (401)
        try:
            validate_signature(body_bytes, signature, _SETTINGS.webhook_secret)
        except WebhookAuthError:
            # Don't echo exception text — avoid leaking which check failed.
            logger.warning("Webhook signature validation failed")
            return _response(401, {"error": "unauthorized"})

        # 2. Parse body (400)
        try:
            payload = parse_webhook_body(body_bytes)
        except WebhookParseError as exc:
            logger.info("Webhook body rejected: %s", exc)
            return _response(400, {"error": "bad_request", "detail": str(exc)})

        # 3. Run async pipeline (200 / 500)
        try:
            return asyncio.run(_run_pipeline(_SETTINGS, payload))
        except (
            Phase1PipelineError,
            Phase2PipelineError,
            JiraAPIError,
            S3UploadError,
            S3PresignError,
        ) as exc:
            logger.exception("Pipeline failure: %s", exc)
            return _response(500, {"error": "server_error"})
        except ValueError as exc:
            # e.g. S3Service rejects a malformed story_id.
            logger.exception("Pipeline rejected input: %s", exc)
            return _response(500, {"error": "server_error"})
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        logger.exception("Unhandled exception in lambda_handler: %s", exc)
        return _response(500, {"error": "server_error"})
