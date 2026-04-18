.PHONY: test lint deploy-dev deploy-prod bootstrap-ssm

test:
	pytest

lint:
	ruff check lambdas/ tests/
	ruff format --check lambdas/ tests/

deploy-dev:
	cd infra && cdk deploy --context env=dev --require-approval never

deploy-prod:
	cd infra && cdk deploy --context env=prod

bootstrap-ssm:
	@echo "Writing SSM placeholder parameters for specforge..."
	@python -c "\
import boto3, json; \
client = boto3.client('ssm'); \
params = [\
  '/specforge/anthropic_api_key',\
  '/specforge/jira_url',\
  '/specforge/jira_email',\
  '/specforge/jira_api_token',\
  '/specforge/s3_bucket',\
  '/specforge/quality_threshold',\
  '/specforge/webhook_secret',\
  '/specforge/agent/quality_id',\
  '/specforge/agent/ambiguity_id',\
  '/specforge/agent/complexity_id',\
  '/specforge/agent/architecture_id',\
  '/specforge/agent/api_id',\
  '/specforge/agent/edge_cases_id',\
  '/specforge/agent/testing_id',\
  '/specforge/agent/writer_id',\
  '/specforge/agents_initialized',\
]; \
[client.put_parameter(Name=p, Value='PLACEHOLDER', Type='SecureString', Overwrite=False) \
 for p in params]; \
print('Done. Update PLACEHOLDER values with real credentials.')\
"
