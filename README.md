# specforge.io

**Automated technical-spec generation for Jira stories, powered by a multi-agent Claude pipeline on AWS Lambda.**

specforge.io is an event-driven service that listens for Jira issue webhooks, runs the story through a chain of specialised Claude agents, and — if the story is good enough — writes a complete, ready-to-implement technical specification back to Jira as a comment + `SPEC.md` attachment.

---

## Table of Contents

1. [What the agents actually do](#what-the-agents-actually-do)
2. [System architecture](#system-architecture)
3. [Repository layout](#repository-layout)
4. [Prerequisites](#prerequisites)
5. [Local development](#local-development)
6. [Configuration & secrets (SSM)](#configuration--secrets-ssm)
7. [Deployment (dev & prod)](#deployment-dev--prod)
8. [Wiring up the Jira webhook](#wiring-up-the-jira-webhook)
9. [Verifying the deployment](#verifying-the-deployment)
10. [Operations & troubleshooting](#operations--troubleshooting)

---

## What the agents actually do

specforge runs **two phases** of agents against every incoming Jira story. Phase 1 is a quality gate; Phase 2 produces the spec, but only if Phase 1 passes.

### Phase 1 — evaluation / quality gate

Three agents score the story independently in parallel. Each returns a structured `AgentScore` with a score (0–10), rationale, and concrete suggestions.

| Agent | Role | Checks for |
|-------|------|------------|
| **Quality Agent** | INVEST-style story quality | Independent, Negotiable, Valuable, Estimable, Small, Testable. Fires low scores on missing acceptance criteria, vague value statements, monolithic stories. |
| **Ambiguity Agent** | Clarity & unambiguous language | Undefined terms, implicit assumptions, missing actors, conflicting requirements, vague verbs ("handle", "support", "process"). |
| **Complexity Agent** | Size & risk estimation | Cross-service touchpoints, unbounded scope, hidden integrations, data-migration risk, reasonableness of story points. |

The three scores are combined into a **composite score** with fixed weights: **Quality 40% / Ambiguity 35% / Complexity 25%**, rounded to 2 decimal places. This composite is compared against `quality_threshold` (SSM-configured, typically `7.0`).

- **Composite < threshold** → the pipeline **stops**. A comment is posted to the Jira issue explaining why, listing each agent's rationale and suggestions. No spec is written.
- **Composite ≥ threshold** → Phase 2 runs.

This gate exists to keep the agents from burning tokens (and polluting Jira) on stories that aren't ready.

### Phase 2 — spec generation

Four agents run in parallel, each producing one section of the final Markdown spec. They receive both the original story and the Phase 1 result, so they can tailor the section to the story's weak points.

| Agent | Produces | Focus |
|-------|----------|-------|
| **Architecture Agent** | `## Architecture` | Component diagram (Mermaid), data flow, tech-stack fit, non-functional considerations (latency, scaling, failure modes). |
| **API Agent** | `## API Design` | Endpoint signatures, request/response schemas, error codes, auth, versioning, idempotency. |
| **Edge Cases Agent** | `## Edge Cases` | Boundary conditions, failure modes, race conditions, concurrency, partial-failure recovery, security pitfalls. |
| **Testing Agent** | `## Testing Strategy` | Unit / integration / e2e matrix, fixtures, mocking boundaries, coverage targets, acceptance-criteria-to-test mapping. |

A pure-function **writer** (`pipeline/writer.py`) assembles the pieces into a single Markdown document, in a **fixed section order** (so diffs stay stable):

```
# <Story Title>
## Story
## Evaluation Summary      ← Phase 1 scores + composite
## Architecture            ← Phase 2
## API Design              ← Phase 2
## Implementation Steps    ← derived
## Edge Cases              ← Phase 2
## Testing Strategy        ← Phase 2
## Definition of Done
```

### Where the output goes

1. **S3**: uploaded to `s3://<spec-bucket>/specs/{story_id}/{YYYY-MM-DD}/SPEC.md` (versioned, SSE-KMS encrypted, private).
2. **Jira**: posted back to the issue both as a **comment** (summary + pre-signed S3 link) and as a file **attachment** (`SPEC.md`).

### End-to-end trigger

1. A Jira automation rule fires a webhook when a story enters (or is updated in) a configured status (e.g. *Ready for Spec*).
2. API Gateway HTTP API → Lambda orchestrator.
3. Lambda validates HMAC signature, parses payload, runs Phase 1, optionally Phase 2, writes output.
4. The whole round-trip typically completes in 30–90s depending on model latency.

---

## System architecture

```
┌─────────┐    webhook (HMAC-signed)   ┌────────────────┐
│  Jira   │ ─────────────────────────▶ │ API Gateway    │
└─────────┘                            │ HTTP API       │
     ▲                                 │ POST /webhook  │
     │                                 └───────┬────────┘
     │ comment + SPEC.md attachment            │
     │                                         ▼
     │                              ┌────────────────────┐
     │                              │ Lambda             │
     │                              │ orchestrator       │
     │                              │ (Python 3.12,      │
     │                              │  1024MB, 5min TO)  │
     │                              └───────┬────────────┘
     │                                      │
     │              ┌──────────┬─────────────┴────────┬──────────┐
     │              ▼          ▼                      ▼          ▼
     │        ┌─────────┐ ┌──────────┐          ┌─────────┐ ┌──────────┐
     │        │  SSM    │ │ Anthropic│          │  S3     │ │CloudWatch│
     │        │ Param   │ │   API    │          │ Bucket  │ │  Logs +  │
     │        │ Store   │ │ (Claude) │          │ (specs) │ │  Alarms  │
     │        └─────────┘ └──────────┘          └────┬────┘ └──────────┘
     │                                                │
     └────────────────────────────────────────────────┘
                       pre-signed URL in comment
```

### AWS resources (created by CDK — see [infra/stacks/specforge_stack.py](infra/stacks/specforge_stack.py))

| Resource | Details |
|----------|---------|
| **Lambda** `OrchestratorFn` | Python 3.12, 1024 MB, 300s timeout, handler `handler.lambda_handler`. |
| **API Gateway** `SpecforgeApi` | HTTP API, single route `POST /webhook`. |
| **S3** `SpecBucket` | Versioned, `KMS_MANAGED` encryption, `BLOCK_ALL` public access, `RETAIN` removal policy. |
| **SSM Parameters** `/specforge/*` | All placeholders at deploy time; populated by `make bootstrap-ssm` and filled with real secrets out-of-band (see below). |
| **IAM Role** `OrchestratorRole` | `ssm:GetParameter` on `arn:aws:ssm:…:parameter/specforge/*`; `s3:PutObject` / `s3:GetObject` on the spec bucket; managed basic-execution policy for logs. |
| **CloudWatch Alarm** `ErrorRateAlarm` | `(errors / invocations) * 100 > 5` over a 5-minute window, single evaluation period, missing-data = not-breaching. |

> **SecureString note.** CloudFormation does not support `SecureString` SSM parameters. The stack creates them as plain `String`s with value `PLACEHOLDER`; the `bootstrap-ssm` target and/or manual `aws ssm put-parameter --type SecureString --overwrite` populate the real values. **Never commit or log real secrets.**

---

## Repository layout

```
specforge.io/
├── Makefile                       # test / lint / deploy-dev / deploy-prod / bootstrap-ssm
├── pyproject.toml                 # python 3.12, pytest, ruff, coverage, deps
├── uv.lock                        # reproducible lockfile (uv)
├── TODO.md                        # task roadmap & progress
├── infra/                         # AWS CDK app
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   └── stacks/specforge_stack.py
├── lambdas/
│   └── orchestrator/              # Lambda deployment package
│       ├── handler.py             # lambda_handler entry point
│       ├── requirements.txt
│       ├── core/                  # models, config, scoring, webhook HMAC
│       ├── agents/                # phase1/ and phase2/ Claude agents + registry
│       ├── pipeline/              # orchestration (phase1, phase2, writer)
│       └── services/              # ssm_service, jira_service, s3_service
├── scripts/
│   └── bootstrap_ssm.py           # seed /specforge/* placeholders (idempotent)
└── tests/
    ├── conftest.py                # moto + env fixtures
    ├── unit/
    └── integration/
```

---

## Prerequisites

You'll need:

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — used for dependency management (`uv sync` reads `uv.lock`). `pip` also works.
- **AWS CDK v2** — `npm i -g aws-cdk` (or use `npx aws-cdk`).
- **AWS credentials** with rights to deploy CloudFormation, Lambda, API Gateway, S3, SSM, IAM, and CloudWatch in the target account/region.
- **An Anthropic API key** with access to the Claude model you want to target.
- **A Jira Cloud site** with a user that has permission to comment on and attach files to issues, plus an API token.

One-time per account/region, if you've never used CDK before:

```bash
npx aws-cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
```

---

## Local development

### Prerequisites

Install [uv](https://docs.astral.sh/uv/) if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Set rebase as the default git pull strategy (avoids divergent-branch errors):

```bash
git config --global pull.rebase true
```

### Setup & run

```bash
# clone + enter the repo
git clone <this-repo>
cd specforge.io

# install all python deps (dev group includes pytest, moto, ruff, etc.)
uv sync

# run unit + integration tests (moto stubs AWS; no real calls)
make test
# equivalently: uv run pytest

# skip the CDK infra test if aws-cdk-lib is not installed
uv run pytest --ignore=tests/unit/test_infra_stack.py

# lint & format check
make lint
```

Tests use `moto` for S3/SSM and `respx` for HTTP mocks; nothing hits real AWS or Anthropic.

> **Note:** `make test` calls `uv run pytest` internally, so there is no need to activate the virtual environment manually.

### Project structure conventions

- `lambdas/orchestrator/` **is** the Lambda package — `cdk.Code.from_asset` points straight at it, so `requirements.txt` and all runtime imports live inside that directory.
- Tests rely on `pythonpath = ["lambdas/orchestrator"]` in `pyproject.toml`, so imports in tests mirror imports in the Lambda (`from core.models import ...`).
- Shared helper scripts (like `bootstrap_ssm.py`) run with `PYTHONPATH=lambdas/orchestrator` so they can reuse `services.ssm_service`.

---

## Configuration & secrets (SSM)

All runtime configuration lives in **AWS Systems Manager Parameter Store** under `/specforge/*`. The Lambda reads them on cold start via `SSMService`.

| Parameter | Type | Purpose |
|-----------|------|---------|
| `/specforge/anthropic_api_key` | SecureString | Anthropic API key. |
| `/specforge/jira_url` | String | Base URL, e.g. `https://your-org.atlassian.net`. |
| `/specforge/jira_email` | String | Email of the Jira user used for Basic Auth. |
| `/specforge/jira_api_token` | SecureString | Jira Cloud API token. |
| `/specforge/webhook_secret` | SecureString | Shared secret used to HMAC-sign webhook bodies. |
| `/specforge/s3_bucket` | String | Name of the spec bucket (set this after `cdk deploy` reports the bucket name). |
| `/specforge/quality_threshold` | String | Phase 1 composite cutoff (e.g. `7.0`). |
| `/specforge/agent/quality_id` | String | Claude agent/model identifier for the Quality agent. |
| `/specforge/agent/ambiguity_id` | String | …Ambiguity agent. |
| `/specforge/agent/complexity_id` | String | …Complexity agent. |
| `/specforge/agent/architecture_id` | String | …Architecture agent. |
| `/specforge/agent/api_id` | String | …API agent. |
| `/specforge/agent/edge_cases_id` | String | …Edge Cases agent. |
| `/specforge/agent/testing_id` | String | …Testing agent. |
| `/specforge/agent/writer_id` | String | …Writer agent / model. |
| `/specforge/agents_initialized` | String | Set to `"true"` once real agent IDs are written (sanity flag). |

### Seeding with `make bootstrap-ssm`

After the stack is deployed, run:

```bash
make bootstrap-ssm
```

This calls [scripts/bootstrap_ssm.py](scripts/bootstrap_ssm.py), which uses `SSMService.bootstrap_agent_ids` to **create any missing** `/specforge/*` parameters with the value `PLACEHOLDER`. It **never overwrites** existing values — safe to run repeatedly.

### Populating real values

Secrets must be `SecureString`. Use the AWS CLI (not CloudFormation):

```bash
aws ssm put-parameter \
  --name /specforge/anthropic_api_key \
  --type SecureString --overwrite \
  --value "sk-ant-..."

aws ssm put-parameter \
  --name /specforge/jira_api_token \
  --type SecureString --overwrite \
  --value "..."

aws ssm put-parameter \
  --name /specforge/webhook_secret \
  --type SecureString --overwrite \
  --value "$(openssl rand -hex 32)"

aws ssm put-parameter \
  --name /specforge/jira_url \
  --type String --overwrite \
  --value "https://your-org.atlassian.net"

aws ssm put-parameter \
  --name /specforge/jira_email \
  --type String --overwrite \
  --value "bot-user@your-org.com"

aws ssm put-parameter \
  --name /specforge/quality_threshold \
  --type String --overwrite \
  --value "7.0"

aws ssm put-parameter \
  --name /specforge/s3_bucket \
  --type String --overwrite \
  --value "<bucket name from cdk output>"

# repeat for each /specforge/agent/*_id
```

Keep the webhook secret: you'll paste it into the Jira automation rule.

---

## Deployment (dev & prod)

Two Make targets wrap CDK:

```bash
make deploy-dev      # cdk deploy --context env=dev --require-approval never
make deploy-prod     # cdk deploy --context env=prod   (prompts before IAM changes)
```

### First-time deploy (dev)

```bash
# 0. one-time per account/region
npx aws-cdk bootstrap

# 1. install infra deps
pip install -r infra/requirements.txt

# 2. sanity check the synthesis
cd infra && cdk synth --context env=dev && cd ..

# 3. deploy
make deploy-dev

# 4. seed SSM placeholders
make bootstrap-ssm

# 5. replace placeholders with real values (see previous section)

# 6. grab the API Gateway URL from the CDK output — this is your webhook URL
#    (it looks like https://<id>.execute-api.<region>.amazonaws.com/webhook)
```

### Prod deploy

Identical flow, but use `make deploy-prod`. CDK will **prompt** before applying IAM changes — review carefully. Secrets must be re-populated in the prod account's SSM (they're account-scoped).

### Rollback

```bash
cd infra && cdk deploy <stack> --rollback
# or point-in-time:
aws cloudformation rollback-stack --stack-name SpecforgeStack
```

The S3 bucket has `removal_policy=RETAIN` and is versioned, so destroy won't delete spec history — you must empty it manually if you truly want to tear down.

---

## Wiring up the Jira webhook

1. In Jira: **Project settings → Automation → Create rule**.
2. **Trigger:** *Issue transitioned* (or whatever fits your workflow — e.g., transition into "Ready for Spec").
3. **Action:** *Send web request*.
   - **URL:** the API Gateway URL from step 6 above (`.../webhook`).
   - **HTTP method:** `POST`.
   - **Headers:**
     - `Content-Type: application/json`
     - `X-Specforge-Signature: {{hmac_sha256_of_body_using_webhook_secret}}`
       > Jira automation doesn't natively HMAC payloads. Options:
       > (a) Fire-and-forget via a proxy Lambda/EdgeFunction that signs and forwards, or
       > (b) Use a Jira Forge app, or
       > (c) Run a minimal signer service — see the `core/webhook.py` contract.
   - **Body (custom data):**
     ```json
     {
       "issue_key": "{{issue.key}}",
       "issue_summary": "{{issue.summary}}",
       "issue_description": "{{issue.description}}",
       "project_key": "{{issue.project.key}}"
     }
     ```
4. Save & enable the rule.

The Lambda will return:
- **401** if the HMAC signature is missing or wrong.
- **400** if the body can't be parsed into a `WebhookPayload`.
- **200** in all other cases (including gate-failed stories — see CloudWatch logs for reasoning).
- **500** on unhandled exceptions.

---

## Verifying the deployment

```bash
# quick smoke test with a real (but dummy) signed payload
export SECRET="<value of /specforge/webhook_secret>"
export URL="https://<id>.execute-api.<region>.amazonaws.com/webhook"
export BODY='{"issue_key":"DEMO-1","issue_summary":"hi","issue_description":"test","project_key":"DEMO"}'
export SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -binary | xxd -p -c 256)

curl -i "$URL" \
  -H "Content-Type: application/json" \
  -H "X-Specforge-Signature: $SIG" \
  --data "$BODY"
```

Then check:

```bash
aws logs tail /aws/lambda/<OrchestratorFn name> --follow
aws s3 ls s3://<spec-bucket>/specs/DEMO-1/ --recursive
```

You should see the evaluation, and — if the dummy story clears the gate — a `SPEC.md` in S3 and a new comment/attachment on `DEMO-1`.

---

## Operations & troubleshooting

### Where things live
- **Runtime logs:** CloudWatch log group `/aws/lambda/<OrchestratorFn>`.
- **Alarms:** `ErrorRateAlarm` on the Lambda — fires when `errors/invocations > 5%` over 5 min.
- **Spec artifacts:** `s3://<spec-bucket>/specs/{story_id}/{YYYY-MM-DD}/SPEC.md` (versioned).

### Common failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| All requests `401` | Webhook signature mismatch | Confirm signer uses the exact SSM value of `/specforge/webhook_secret`, `sha256`, and raw request body bytes. |
| All requests `400` | Jira payload shape changed | Check `core/webhook.py::parse_webhook_body` — required fields: `issue_key`, `issue_summary`, `issue_description`, `project_key`. |
| Lambda 500 + `SSMError` in logs | SSM placeholder still set | Replace `PLACEHOLDER` with real value for the parameter named in the log. |
| Lambda 500 + `AgentEvaluationError` | Claude call failed (rate limit, bad key, network) | Check Anthropic dashboard, rotate `/specforge/anthropic_api_key` if needed. |
| 200 but no Jira comment | `JiraAPIError` — check logs | Verify `/specforge/jira_email` + `/specforge/jira_api_token` + `/specforge/jira_url` and Jira user's comment/attach permissions. |
| Gate always fails | `quality_threshold` too strict, or stories genuinely poor | Temporarily lower `/specforge/quality_threshold`; inspect rationales in the Jira comment. |

### Cost & performance notes
- Lambda runs up to ~5 min; actual spec generation is usually 30–90s. Phase 1 agents (`asyncio.gather`) overlap; Phase 2 agents likewise.
- Memory is 1024 MB — raise if you hit timeouts or if Anthropic response parsing becomes slow.
- S3 versioning + SSE-KMS costs are negligible at typical spec volumes.

---

## License

**Proprietary — All Rights Reserved.** Copyright (c) 2026 Elad Zoarets.

This software is **not** open source. No license to use, copy, modify, deploy, or distribute the Software is granted by default. Any use requires prior, specific, written permission from the copyright holder.

To request permission, contact **Elad Zoarets &lt;eladzoarets@gmail.com&gt;**.

See [LICENSE](LICENSE) for the full terms.
