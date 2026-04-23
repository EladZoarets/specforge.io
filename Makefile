.PHONY: test lint deploy-dev deploy-prod bootstrap-ssm

test:
	uv run pytest

lint:
	ruff check lambdas/ tests/
	ruff format --check lambdas/ tests/

deploy-dev:
	cd infra && PATH=$(shell pwd)/.venv/bin:$$PATH cdk deploy --context env=dev --require-approval never

deploy-prod:
	cd infra && PATH=$(shell pwd)/.venv/bin:$$PATH cdk deploy --context env=prod

bootstrap-ssm:
	@echo "Writing SSM placeholder parameters for specforge..."
	PYTHONPATH=lambdas/orchestrator:$$PYTHONPATH uv run python scripts/bootstrap_ssm.py
