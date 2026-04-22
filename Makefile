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
	PYTHONPATH=lambdas/orchestrator python scripts/bootstrap_ssm.py
