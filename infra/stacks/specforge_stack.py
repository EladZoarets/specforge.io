import os
from typing import Any

import aws_cdk as cdk
from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_ssm as ssm,
)
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

# CloudFormation does not support SecureString parameters. Sensitive params
# (/specforge/jira_token, /specforge/webhook_secret, /specforge/anthropic_api_key)
# are written as String/PLACEHOLDER here and replaced with SecureString values
# by `make bootstrap-ssm` (TASK-014) using the AWS SDK directly.
_SSM_PARAMS = [
    "/specforge/jira_url",
    "/specforge/jira_token",
    "/specforge/webhook_secret",
    "/specforge/anthropic_api_key",
]


class SpecforgeStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        spec_bucket = s3.Bucket(
            self,
            "SpecBucket",
            versioned=True,
            encryption=s3.BucketEncryption.KMS_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        for param_name in _SSM_PARAMS:
            ssm.StringParameter(
                self,
                f"Param{param_name.split('/')[-1].replace('_', '').title()}",
                parameter_name=param_name,
                string_value="PLACEHOLDER",
            )

        role = iam.Role(
            self,
            "OrchestratorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    cdk.Arn.format(
                        cdk.ArnComponents(
                            service="ssm",
                            resource="parameter",
                            resource_name="specforge/*",
                        ),
                        self,
                    )
                ],
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetObject"],
                resources=[spec_bucket.bucket_arn + "/*"],
            )
        )

        fn = lambda_.Function(
            self,
            "OrchestratorFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../lambdas/orchestrator")
            ),
            timeout=cdk.Duration.seconds(300),
            memory_size=1024,
            role=role,
        )

        integration = HttpLambdaIntegration("WebhookIntegration", fn)

        http_api = apigwv2.HttpApi(self, "SpecforgeApi")

        http_api.add_routes(
            path="/webhook",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )

        error_rate = cw.MathExpression(
            expression="100 * errors / invocations",
            using_metrics={
                "errors": fn.metric_errors(period=cdk.Duration.minutes(5)),
                "invocations": fn.metric_invocations(period=cdk.Duration.minutes(5)),
            },
            period=cdk.Duration.minutes(5),
            label="Error Rate (%)",
        )

        cw.Alarm(
            self,
            "ErrorRateAlarm",
            metric=error_rate,
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Lambda error rate exceeded 5% over 5 minutes",
        )
