# specforge.io — Task List

## Epic: Jira Story Ingestion and Automated Spec Generation Pipeline

---

## Execution Order

| Wave | Tasks | Notes |
|------|-------|-------|
| 1 | TASK-001 | Unblocks everything |
| 2 | TASK-002, TASK-003, TASK-013 | Parallel |
| 3 | TASK-004, TASK-005, TASK-006, TASK-007, TASK-010, TASK-011 | Parallel |
| 4 | TASK-008 | Needs TASK-005 + TASK-006 |
| 5 | TASK-009 | Needs TASK-007 + TASK-008 |
| 6 | TASK-014 | Needs TASK-003 + TASK-013 |
| 7 | TASK-012 | Needs all pipeline + service tasks |

---

## Tasks

### TASK-001 — Project Scaffold and Python Toolchain
- [x] `pyproject.toml` — pytest, pytest-cov, moto, anthropic, boto3, python-dotenv
- [x] `Makefile` — targets: `test`, `lint`, `deploy-dev`, `deploy-prod`, `bootstrap-ssm`
- [x] `.gitignore` — .env, __pycache__, cdk.out/, .venv/, dist/
- [x] `lambdas/orchestrator/requirements.txt`
- [x] `infra/requirements.txt`
- [x] `tests/conftest.py` — pytest fixtures, moto setup, env patching
- [x] All `__init__.py` files across every package

**Complexity:** S | **Dependencies:** none

---

### TASK-002 — Core Models (`core/models.py`)
- [ ] `JiraStory` — id, title, description, acceptance_criteria, story_points
- [ ] `AgentScore` — agent_name, score (0–10 validated), rationale, suggestions
- [ ] `Phase1Result` — quality, ambiguity, complexity scores + composite_score + passed_gate
- [ ] `Phase2Result` — architecture, api_design, edge_cases, testing_strategy sections
- [ ] `SpecDocument` — story_id, phase1, phase2, spec_markdown, s3_key
- [ ] `WebhookPayload` — issue_key, issue_summary, issue_description, project_key
- [ ] Pydantic v2 field_validator: score must be 0–10

**Complexity:** S | **Dependencies:** TASK-001

---

### TASK-003 — Config and SSM Service (`core/config.py`, `services/ssm_service.py`)
- [ ] `Settings` dataclass — all required env vars, raises EnvironmentError listing all missing
- [ ] `load_settings()` — reads from os.environ, fails fast
- [ ] `SSMService` class — injectable boto3 client, get_parameter, put_parameter
- [ ] `SSMError` domain exception
- [ ] `tests/unit/test_config.py` — all vars present, one missing, multiple missing

**Complexity:** S | **Dependencies:** TASK-001

---

### TASK-004 — Webhook Receiver and HMAC Validation (`core/webhook.py`)
- [ ] `validate_signature(payload_body, signature_header, secret)` — hmac.compare_digest
- [ ] `parse_webhook_body(body)` → `WebhookPayload`
- [ ] `WebhookAuthError` (401) and `WebhookParseError` (400) exception types
- [ ] `tests/unit/test_webhook.py` — valid sig, wrong sig, missing header, good body, missing key

**Complexity:** S | **Dependencies:** TASK-002, TASK-003

---

### TASK-005 — Scoring Logic (`core/scoring.py`)
- [ ] `compute_composite(quality, ambiguity, complexity)` — weights 40/35/25, rounded 2dp
- [ ] `evaluate_gate(composite, threshold)` → bool
- [ ] `build_phase1_result(quality, ambiguity, complexity, threshold)` → Phase1Result
- [ ] `tests/unit/test_scoring.py` — weights sum to 1, gate boundary, known input assertion

**Complexity:** S | **Dependencies:** TASK-002

---

### TASK-006 — Phase 1 Agents (Quality, Ambiguity, Complexity)
- [ ] `agents/registry.py` — AgentRegistry with register/get, KeyError on missing
- [ ] `agents/phase1/quality_agent.py` — INVEST scoring, SYSTEM_PROMPT constant, AgentEvaluationError
- [ ] `agents/phase1/ambiguity_agent.py` — clarity scoring, SYSTEM_PROMPT constant
- [ ] `agents/phase1/complexity_agent.py` — size + risk estimation, SYSTEM_PROMPT constant
- [ ] Each agent: `async def evaluate(story) -> AgentScore`, parses JSON response
- [ ] `AgentEvaluationError` distinct exception type

**Complexity:** M | **Dependencies:** TASK-002, TASK-003

---

### TASK-007 — Phase 2 Agents (Architecture, API, Edge Cases, Testing)
- [ ] `agents/phase2/architecture_agent.py` — SYSTEM_PROMPT, thinking: adaptive
- [ ] `agents/phase2/api_agent.py` — SYSTEM_PROMPT, thinking: adaptive
- [ ] `agents/phase2/edge_cases_agent.py` — SYSTEM_PROMPT, thinking: adaptive
- [ ] `agents/phase2/testing_agent.py` — SYSTEM_PROMPT, thinking: adaptive
- [ ] Each agent: `async def generate(story, phase1) -> str` (Markdown section)
- [ ] `AgentGenerationError` distinct exception type

**Complexity:** M | **Dependencies:** TASK-002, TASK-003

---

### TASK-008 — Phase 1 Pipeline (`pipeline/phase1.py`)
- [ ] `run_phase1(story, agents, threshold)` — asyncio.gather x3
- [ ] Wraps AgentEvaluationError → Phase1PipelineError with agent name
- [ ] `tests/integration/test_phase1.py` — all pass, one timeout, gate boundary x2

**Complexity:** S | **Dependencies:** TASK-005, TASK-006

---

### TASK-009 — Phase 2 Pipeline and Writer (`pipeline/phase2.py`, `pipeline/writer.py`)
- [ ] `assemble_spec(story, phase1, phase2)` — pure function, fixed section order
- [ ] Section order: Story → Evaluation Summary → Architecture → API Design → Implementation Steps → Edge Cases → Testing Strategy → Definition of Done
- [ ] `run_phase2(story, phase1, agents, threshold)` — asyncio.gather x4, returns None if gate failed
- [ ] Wraps AgentGenerationError → Phase2PipelineError
- [ ] `tests/unit/test_writer.py` — all 6 headers present, score in summary, gate fail text
- [ ] `tests/integration/test_phase2.py` — gate=False skips agents, happy path, one agent error

**Complexity:** M | **Dependencies:** TASK-007, TASK-008

---

### TASK-010 — Jira Service (`services/jira_service.py`)
- [ ] `JiraService` — injectable httpx.AsyncClient, Basic Auth
- [ ] `post_comment(issue_key, body)` — POST /rest/api/3/issue/{key}/comment
- [ ] `attach_file(issue_key, content_bytes, filename)` — X-Atlassian-Token: no-check
- [ ] `JiraAPIError` domain exception, no auth headers in logs
- [ ] `tests/integration/test_jira_service.py` — correct URL, auth header, attach header, non-2xx x2

**Complexity:** S | **Dependencies:** TASK-002

---

### TASK-011 — S3 Service (`services/s3_service.py`)
- [ ] `S3Service` — injectable boto3 client
- [ ] `upload_spec(story_id, spec_markdown)` — key: specs/{id}/{YYYY-MM-DD}/SPEC.md, ContentType: text/markdown
- [ ] `generate_presigned_url(key, expires_in)` → str
- [ ] `S3UploadError` domain exception, retry x2 with backoff
- [ ] `tests/integration/test_s3_service.py` — correct key, ContentType, presigned URL, ClientError

**Complexity:** S | **Dependencies:** TASK-003

---

### TASK-012 — Lambda Handler (`handler.py`)
- [ ] `lambda_handler(event, context)` — full wiring of all pipeline stages
- [ ] Returns 401 on WebhookAuthError, 400 on WebhookParseError, 200 on all other paths
- [ ] Gate fail → post_comment + return 200
- [ ] Gate pass → run_phase2 → upload_spec → attach_file → return 200
- [ ] Top-level exception handler → 500
- [ ] `tests/integration/test_handler.py` — invalid sig, parse fail, gate fail, happy path, exception

**Complexity:** M | **Dependencies:** TASK-004, TASK-008, TASK-009, TASK-010, TASK-011

---

### TASK-013 — CDK Infra Stack
- [ ] `infra/app.py` — CDK entry point
- [ ] `infra/cdk.json` — app: "python app.py"
- [ ] `infra/stacks/specforge_stack.py`:
  - [ ] Lambda (Python 3.12, 5 min timeout, 1024 MB)
  - [ ] API Gateway HTTP API — POST /webhook route
  - [ ] S3 bucket (versioned, private, SSE-KMS)
  - [ ] SSM parameters (PLACEHOLDER values)
  - [ ] IAM role: ssm:GetParameter on /specforge/*, s3:PutObject+GetObject on spec bucket
  - [ ] CloudWatch alarm: error rate > 5% over 5 min
- [ ] `cdk synth` exits 0

**Complexity:** M | **Dependencies:** TASK-001

---

### TASK-014 — SSM Bootstrap and Agent ID Initialization
- [ ] `ssm_service.bootstrap_agent_ids(agent_map, overwrite=False)`
- [ ] `Makefile` `bootstrap-ssm` target — writes all /specforge/* placeholders
- [ ] Does not overwrite existing parameters unless force=True

**Complexity:** S | **Dependencies:** TASK-003, TASK-013

---

## Progress

| Task | Status | PR |
|------|--------|----|
| TASK-001 | ✅ done | — |
| TASK-002 | ⬜ pending | — |
| TASK-003 | ⬜ pending | — |
| TASK-004 | ⬜ pending | — |
| TASK-005 | ⬜ pending | — |
| TASK-006 | ⬜ pending | — |
| TASK-007 | ⬜ pending | — |
| TASK-008 | ⬜ pending | — |
| TASK-009 | ⬜ pending | — |
| TASK-010 | ⬜ pending | — |
| TASK-011 | ⬜ pending | — |
| TASK-012 | ⬜ pending | — |
| TASK-013 | ⬜ pending | — |
| TASK-014 | ⬜ pending | — |
