"""
Integration tests for ``handler.lambda_handler``.

The unit under test is the *wiring*: which components get called in which
order, how exceptions map to HTTP statuses, and how the webhook payload
flows through the pipeline. We mock every external dependency (Anthropic
client, agent constructors, JiraService, S3Service) so the tests run
offline and deterministically.

Handler is imported *inside* each test (after ``base_env`` has set the
env vars) to guarantee module-level init sees a fully-populated
environment. Between tests we pop it from ``sys.modules`` to avoid
carrying state across test cases.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.models import AgentScore, Phase1Result, Phase2Result

WEBHOOK_SECRET = "test-webhook-secret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _valid_body() -> dict[str, Any]:
    return {
        "issue_key": "SPEC-1",
        "issue_summary": "Build a webhook receiver",
        "issue_description": "As a user, I want a webhook receiver so that ...",
        "project_key": "SPEC",
    }


def _event(body: dict[str, Any] | str | bytes, *, signed: bool = True) -> dict[str, Any]:
    if isinstance(body, dict):
        raw = json.dumps(body)
    elif isinstance(body, bytes):
        raw = body.decode("utf-8")
    else:
        raw = body
    headers: dict[str, str] = {}
    if signed:
        headers["x-hub-signature-256"] = _sign(raw.encode("utf-8"))
    else:
        headers["x-hub-signature-256"] = "sha256=deadbeef"
    return {"body": raw, "headers": headers, "isBase64Encoded": False}


def _score(name: str, value: float, suggestions: list[str] | None = None) -> AgentScore:
    return AgentScore(
        agent_name=name,
        score=value,
        rationale=f"{name} rationale",
        suggestions=suggestions or [],
    )


def _pass_phase1() -> Phase1Result:
    # Composite: 9.0*0.40 + 8.5*0.35 + 8.0*0.25 = 8.575 (recomputed by pipeline).
    return Phase1Result(
        quality=_score("quality", 9.0),
        ambiguity=_score("ambiguity", 8.5),
        complexity=_score("complexity", 8.0),
        composite_score=8.575,
        passed_gate=True,
    )


def _fail_phase1() -> Phase1Result:
    # Composite: 4.0*0.40 + 3.0*0.35 + 5.0*0.25 = 3.90 (recomputed by pipeline).
    return Phase1Result(
        quality=_score("quality", 4.0, ["Add acceptance criteria"]),
        ambiguity=_score("ambiguity", 3.0, ["Clarify the success metric"]),
        complexity=_score("complexity", 5.0, ["Split into smaller stories"]),
        composite_score=3.90,
        passed_gate=False,
    )


def _phase2_result() -> Phase2Result:
    return Phase2Result(
        architecture="Component overview text.",
        api_design="API design text.",
        edge_cases="Edge cases text.",
        testing_strategy="Testing strategy text.",
    )


@pytest.fixture
def handler_module(base_env, s3_client):  # noqa: ARG001 — fixtures for side effects
    """Import (or reload) ``handler`` with env vars + S3 bucket in place."""
    sys.modules.pop("handler", None)
    module = importlib.import_module("handler")
    yield module
    sys.modules.pop("handler", None)


def _make_agent(result: Any, *, method: str) -> MagicMock:
    instance = MagicMock()
    mock = AsyncMock(return_value=result)
    setattr(instance, method, mock)
    return instance


def _patch_phase1_agents(module: Any, phase1_result: Phase1Result) -> Any:
    """Patch the three Phase 1 agents so they produce ``phase1_result``."""
    q = _make_agent(phase1_result.quality, method="evaluate")
    a = _make_agent(phase1_result.ambiguity, method="evaluate")
    c = _make_agent(phase1_result.complexity, method="evaluate")
    return (
        patch.object(module, "QualityAgent", MagicMock(return_value=q)),
        patch.object(module, "AmbiguityAgent", MagicMock(return_value=a)),
        patch.object(module, "ComplexityAgent", MagicMock(return_value=c)),
    )


def _patch_phase2_agents(module: Any, phase2: Phase2Result) -> Any:
    arch = _make_agent(phase2.architecture, method="generate")
    api = _make_agent(phase2.api_design, method="generate")
    edge = _make_agent(phase2.edge_cases, method="generate")
    test = _make_agent(phase2.testing_strategy, method="generate")
    return (
        patch.object(module, "ArchitectureAgent", MagicMock(return_value=arch)),
        patch.object(module, "ApiAgent", MagicMock(return_value=api)),
        patch.object(module, "EdgeCasesAgent", MagicMock(return_value=edge)),
        patch.object(module, "TestingAgent", MagicMock(return_value=test)),
    )


# ---------------------------------------------------------------------------
# 1. Invalid signature → 401
# ---------------------------------------------------------------------------


def test_invalid_signature_returns_401_and_skips_pipeline(handler_module):
    quality_ctor = MagicMock()
    jira_ctor = MagicMock()
    s3_ctor = MagicMock()

    with (
        patch.object(handler_module, "QualityAgent", quality_ctor),
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "S3Service", s3_ctor),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body(), signed=False), None)

    assert resp["statusCode"] == 401
    assert json.loads(resp["body"]) == {"error": "unauthorized"}
    # Nothing downstream should have been touched.
    assert quality_ctor.call_count == 0
    assert jira_ctor.call_count == 0
    assert s3_ctor.call_count == 0


# ---------------------------------------------------------------------------
# 2-3. Malformed / missing body → 400
# ---------------------------------------------------------------------------


def test_malformed_body_returns_400(handler_module):
    bad = "not-json-at-all"
    event = {
        "body": bad,
        "headers": {"x-hub-signature-256": _sign(bad.encode("utf-8"))},
        "isBase64Encoded": False,
    }
    resp = handler_module.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "bad_request"
    assert "detail" in body


def test_missing_required_field_returns_400(handler_module):
    bad_body = {"issue_key": "SPEC-1"}  # missing summary/description/project_key
    resp = handler_module.lambda_handler(_event(bad_body), None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "bad_request"


# ---------------------------------------------------------------------------
# 4. Gate-fail path → 200, Jira comment posted, S3 untouched
# ---------------------------------------------------------------------------


def test_gate_fail_posts_comment_and_skips_s3(handler_module):
    phase1 = _fail_phase1()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(return_value={})
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    # S3 is module-scoped; patch the instance and assert its methods are
    # never touched on the gate-fail path.
    s3_instance = MagicMock()

    with (
        q_patch,
        a_patch,
        c_patch,
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "_S3_SERVICE", s3_instance),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["message"] == "gate_failed"
    assert body["issue_key"] == "SPEC-1"

    # Comment posted once with feedback, S3 never touched, no attachment.
    jira_instance.post_comment.assert_awaited_once()
    call_args = jira_instance.post_comment.await_args
    assert call_args.args[0] == "SPEC-1"
    comment_body = call_args.args[1]
    assert f"{phase1.composite_score:.2f}" in comment_body  # composite score in body
    assert "Add acceptance criteria" in comment_body  # top suggestion echoed
    jira_instance.attach_file.assert_not_awaited()
    s3_instance.upload_spec.assert_not_called()
    s3_instance.generate_presigned_url.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Gate-pass / happy path
# ---------------------------------------------------------------------------


def test_gate_pass_happy_path(handler_module):
    phase1 = _pass_phase1()
    phase2 = _phase2_result()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)
    arch_p, api_p, edge_p, test_p = _patch_phase2_agents(handler_module, phase2)

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(return_value={})
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    # S3 service is now module-scoped; patch the instance directly.
    s3_instance = MagicMock()
    s3_instance.upload_spec = MagicMock(return_value="specs/SPEC-1/2026-04-22/SPEC.md")
    s3_instance.generate_presigned_url = MagicMock(
        return_value="https://s3.example.com/signed-url"
    )

    with (
        q_patch,
        a_patch,
        c_patch,
        arch_p,
        api_p,
        edge_p,
        test_p,
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "_S3_SERVICE", s3_instance),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["message"] == "spec_generated"
    assert body["issue_key"] == "SPEC-1"
    assert body["s3_key"] == "specs/SPEC-1/2026-04-22/SPEC.md"

    # S3: one upload + one presign.
    s3_instance.upload_spec.assert_called_once()
    uploaded_markdown = s3_instance.upload_spec.call_args.args[1]
    for heading in (
        "## Story",
        "## Evaluation Summary",
        "## Architecture",
        "## API Design",
        "## Implementation Steps",
        "## Edge Cases",
        "## Testing Strategy",
        "## Definition of Done",
    ):
        assert heading in uploaded_markdown, f"missing heading: {heading}"
    s3_instance.generate_presigned_url.assert_called_once_with(
        "specs/SPEC-1/2026-04-22/SPEC.md"
    )

    # Jira: one comment with URL, one file attachment.
    jira_instance.post_comment.assert_awaited_once()
    comment_body = jira_instance.post_comment.await_args.args[1]
    assert "https://s3.example.com/signed-url" in comment_body
    assert "SPEC-1" in comment_body
    jira_instance.attach_file.assert_awaited_once()
    attach_args = jira_instance.attach_file.await_args.args
    assert attach_args[0] == "SPEC-1"
    assert isinstance(attach_args[1], bytes)
    assert attach_args[2] == "SPEC-1-SPEC.md"


# ---------------------------------------------------------------------------
# 6-8. Exceptions during pipeline → 500 with generic body
# ---------------------------------------------------------------------------


def test_phase2_exception_returns_500(handler_module):
    from pipeline.phase2 import Phase2PipelineError

    phase1 = _pass_phase1()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)

    # Phase 2 architecture agent raises.
    failing = MagicMock()
    failing.generate = AsyncMock(side_effect=Phase2PipelineError("architecture", "boom"))
    arch_ctor = MagicMock(return_value=failing)

    api_ok = _make_agent("api text", method="generate")
    edge_ok = _make_agent("edge text", method="generate")
    test_ok = _make_agent("test text", method="generate")

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(return_value={})
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    with (
        q_patch,
        a_patch,
        c_patch,
        patch.object(handler_module, "ArchitectureAgent", arch_ctor),
        patch.object(handler_module, "ApiAgent", MagicMock(return_value=api_ok)),
        patch.object(handler_module, "EdgeCasesAgent", MagicMock(return_value=edge_ok)),
        patch.object(handler_module, "TestingAgent", MagicMock(return_value=test_ok)),
        patch.object(handler_module, "JiraService", jira_ctor),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body == {"error": "server_error"}
    # Generic body must not echo the exception message.
    assert "boom" not in resp["body"]


def test_s3_upload_exception_returns_500(handler_module):
    from services.s3_service import S3UploadError

    phase1 = _pass_phase1()
    phase2 = _phase2_result()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)
    arch_p, api_p, edge_p, test_p = _patch_phase2_agents(handler_module, phase2)

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(return_value={})
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    s3_instance = MagicMock()
    s3_instance.upload_spec = MagicMock(
        side_effect=S3UploadError("b", "k", "InternalError", "put failed")
    )

    with (
        q_patch,
        a_patch,
        c_patch,
        arch_p,
        api_p,
        edge_p,
        test_p,
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "_S3_SERVICE", s3_instance),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 500
    assert json.loads(resp["body"]) == {"error": "server_error"}
    s3_instance.upload_spec.assert_called_once()
    # Jira comment/attachment should NOT have fired — S3 broke first.
    jira_instance.post_comment.assert_not_awaited()
    jira_instance.attach_file.assert_not_awaited()


def test_jira_post_comment_exception_returns_500(handler_module):
    from services.jira_service import JiraAPIError

    phase1 = _pass_phase1()
    phase2 = _phase2_result()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)
    arch_p, api_p, edge_p, test_p = _patch_phase2_agents(handler_module, phase2)

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(
        side_effect=JiraAPIError("Jira API error: POST ... returned 500")
    )
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    s3_instance = MagicMock()
    s3_instance.upload_spec = MagicMock(return_value="specs/SPEC-1/2026-04-22/SPEC.md")
    s3_instance.generate_presigned_url = MagicMock(return_value="https://s3.example/u")

    with (
        q_patch,
        a_patch,
        c_patch,
        arch_p,
        api_p,
        edge_p,
        test_p,
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "_S3_SERVICE", s3_instance),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 500
    assert json.loads(resp["body"]) == {"error": "server_error"}


# ---------------------------------------------------------------------------
# 9. Module-level init failure → 500 on every invocation
# ---------------------------------------------------------------------------


def test_module_init_error_returns_500(handler_module):
    """If ``_INIT_ERROR`` is set, every invocation returns 500 without work."""
    sentinel = RuntimeError("module init failed")
    with patch.object(handler_module, "_INIT_ERROR", sentinel):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 500
    assert json.loads(resp["body"]) == {"error": "server_error"}


def test_module_init_missing_settings_returns_500(handler_module):
    """Also guard the path where _SETTINGS is None for any reason."""
    with patch.object(handler_module, "_SETTINGS", None):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"]) == {"error": "server_error"}


# ---------------------------------------------------------------------------
# 10-11. Oversized body → 400 before HMAC runs (Finding 1)
# ---------------------------------------------------------------------------


def test_oversized_base64_body_rejected_before_hmac(handler_module):
    """A huge base64-encoded body must be rejected at _extract_body, before
    any HMAC check runs — otherwise a malicious sender can OOM the Lambda
    with ``base64.b64decode`` prior to the size check in parse_webhook_body.
    """
    from core.webhook import _MAX_BODY_BYTES

    oversized = "A" * (2 * _MAX_BODY_BYTES + 1)
    event = {
        "body": oversized,
        "headers": {"x-hub-signature-256": "sha256=deadbeef"},
        "isBase64Encoded": True,
    }

    # Patch validate_signature so we can prove it was never invoked.
    sig_mock = MagicMock()
    with patch.object(handler_module, "validate_signature", sig_mock):
        resp = handler_module.lambda_handler(event, None)

    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "bad_request"
    assert "maximum size" in body["detail"]
    # Critical: HMAC must not have run on the oversized input.
    sig_mock.assert_not_called()


def test_oversized_raw_body_rejected(handler_module):
    """Raw (non-b64) body over ``_MAX_BODY_BYTES`` also rejected → 400."""
    from core.webhook import _MAX_BODY_BYTES

    oversized = "X" * (_MAX_BODY_BYTES + 1)
    event = {
        "body": oversized,
        "headers": {"x-hub-signature-256": "sha256=deadbeef"},
        "isBase64Encoded": False,
    }

    sig_mock = MagicMock()
    with patch.object(handler_module, "validate_signature", sig_mock):
        resp = handler_module.lambda_handler(event, None)

    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "bad_request"
    assert "maximum size" in body["detail"]
    sig_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 12. AsyncAnthropic client is closed on happy path (Finding 2)
# ---------------------------------------------------------------------------


def test_anthropic_client_closed_on_happy_path(handler_module):
    """After a successful invocation, the AsyncAnthropic client's async
    context manager must have exited (i.e. the underlying httpx client is
    closed). Otherwise warm invocations leak file descriptors.
    """
    phase1 = _pass_phase1()
    phase2 = _phase2_result()
    q_patch, a_patch, c_patch = _patch_phase1_agents(handler_module, phase1)
    arch_p, api_p, edge_p, test_p = _patch_phase2_agents(handler_module, phase2)

    jira_instance = MagicMock()
    jira_instance.post_comment = AsyncMock(return_value={})
    jira_instance.attach_file = AsyncMock(return_value={})
    jira_ctor = MagicMock(return_value=jira_instance)

    s3_instance = MagicMock()
    s3_instance.upload_spec = MagicMock(return_value="specs/SPEC-1/2026-04-22/SPEC.md")
    s3_instance.generate_presigned_url = MagicMock(return_value="https://s3.example/u")

    # Fake AsyncAnthropic whose async CM tracks __aenter__/__aexit__.
    fake_anthropic = MagicMock()
    fake_anthropic.__aenter__ = AsyncMock(return_value=fake_anthropic)
    fake_anthropic.__aexit__ = AsyncMock(return_value=None)
    anthropic_ctor = MagicMock(return_value=fake_anthropic)

    with (
        q_patch,
        a_patch,
        c_patch,
        arch_p,
        api_p,
        edge_p,
        test_p,
        patch.object(handler_module, "JiraService", jira_ctor),
        patch.object(handler_module, "_S3_SERVICE", s3_instance),
        patch("anthropic.AsyncAnthropic", anthropic_ctor),
    ):
        resp = handler_module.lambda_handler(_event(_valid_body()), None)

    assert resp["statusCode"] == 200
    # Client was constructed, entered, and — critically — exited.
    anthropic_ctor.assert_called_once()
    fake_anthropic.__aenter__.assert_awaited_once()
    fake_anthropic.__aexit__.assert_awaited_once()


# ---------------------------------------------------------------------------
# 13. S3Service is module-scoped and reused across invocations (Finding 3)
# ---------------------------------------------------------------------------


def test_s3_service_module_scoped_reused_across_invocations(base_env, s3_client):  # noqa: ARG001
    """The S3Service instance is constructed once at module import and
    reused across warm invocations. Construction cost (~150ms) must not
    re-incur on every call.
    """
    # Ensure a fresh import so we can intercept the module-level S3Service
    # construction below.
    sys.modules.pop("handler", None)

    s3_instance = MagicMock()
    s3_instance.upload_spec = MagicMock(return_value="specs/SPEC-1/2026-04-22/SPEC.md")
    s3_instance.generate_presigned_url = MagicMock(return_value="https://s3.example/u")

    s3_ctor = MagicMock(return_value=s3_instance)

    # Patch S3Service *before* import so the module-level construction uses
    # our mock exactly once.
    with patch("services.s3_service.S3Service", s3_ctor):
        module = importlib.import_module("handler")
    try:
        phase1 = _pass_phase1()
        phase2 = _phase2_result()
        q_patch, a_patch, c_patch = _patch_phase1_agents(module, phase1)
        arch_p, api_p, edge_p, test_p = _patch_phase2_agents(module, phase2)

        jira_instance = MagicMock()
        jira_instance.post_comment = AsyncMock(return_value={})
        jira_instance.attach_file = AsyncMock(return_value={})
        jira_ctor = MagicMock(return_value=jira_instance)

        with (
            q_patch,
            a_patch,
            c_patch,
            arch_p,
            api_p,
            edge_p,
            test_p,
            patch.object(module, "JiraService", jira_ctor),
        ):
            # Two successive invocations — simulating warm re-entry.
            resp1 = module.lambda_handler(_event(_valid_body()), None)
            resp2 = module.lambda_handler(_event(_valid_body()), None)

        assert resp1["statusCode"] == 200
        assert resp2["statusCode"] == 200

        # Critical: S3Service constructor called exactly once (at import),
        # not per-invocation.
        assert s3_ctor.call_count == 1

        # The same instance handled both uploads.
        assert s3_instance.upload_spec.call_count == 2
    finally:
        sys.modules.pop("handler", None)
