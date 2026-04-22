"""Bootstrap SSM placeholder parameters for specforge.

Intended to be invoked by the ``bootstrap-ssm`` Makefile target with
``PYTHONPATH=lambdas/orchestrator`` so ``services.ssm_service`` is importable.

Existing parameters are left untouched; this script never overwrites.
Populate real values via the AWS console or a follow-up tool.
"""

from __future__ import annotations

from services.ssm_service import SSMService

PLACEHOLDER_VALUE = "PLACEHOLDER"

# Keep this list in sync with infra/runtime expectations. Scope unchanged
# from the previous inline Makefile list.
PARAMETER_NAMES: tuple[str, ...] = (
    "/specforge/anthropic_api_key",
    "/specforge/jira_url",
    "/specforge/jira_email",
    "/specforge/jira_api_token",
    "/specforge/s3_bucket",
    "/specforge/quality_threshold",
    "/specforge/webhook_secret",
    "/specforge/agent/quality_id",
    "/specforge/agent/ambiguity_id",
    "/specforge/agent/complexity_id",
    "/specforge/agent/architecture_id",
    "/specforge/agent/api_id",
    "/specforge/agent/edge_cases_id",
    "/specforge/agent/testing_id",
    "/specforge/agent/writer_id",
    "/specforge/agents_initialized",
)


def main() -> None:
    service = SSMService()
    agent_map = {name: PLACEHOLDER_VALUE for name in PARAMETER_NAMES}
    status = service.bootstrap_agent_ids(agent_map, overwrite=False)
    print("SSM bootstrap status:")
    for name, state in status.items():
        print(f"  {state:>11}  {name}")
    print("Done. Update PLACEHOLDER values with real credentials.")


if __name__ == "__main__":
    main()
