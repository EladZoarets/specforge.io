import aws_cdk as cdk
import pytest
from aws_cdk import assertions

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../infra"))
from stacks.specforge_stack import SpecforgeStack


@pytest.fixture(scope="module")
def template() -> assertions.Template:
    app = cdk.App()
    stack = SpecforgeStack(app, "TestStack")
    return assertions.Template.from_stack(stack)


def test_lambda_runtime_and_sizing(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Timeout": 300,
            "MemorySize": 1024,
            "Handler": "handler.lambda_handler",
        },
    )


def test_s3_versioning_and_encryption(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}
                ]
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_ssm_parameters_placeholder(template: assertions.Template) -> None:
    params = [
        "/specforge/jira_url",
        "/specforge/jira_token",
        "/specforge/webhook_secret",
        "/specforge/anthropic_api_key",
    ]
    for name in params:
        template.has_resource_properties(
            "AWS::SSM::Parameter",
            {"Name": name, "Value": "PLACEHOLDER", "Type": "String"},
        )


def test_api_gateway_post_webhook_route(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "POST /webhook"},
    )


def test_cloudwatch_alarm_threshold(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Threshold": 5,
            "EvaluationPeriods": 1,
            "ComparisonOperator": "GreaterThanThreshold",
            "TreatMissingData": "notBreaching",
        },
    )


def test_iam_ssm_policy_scoped_to_specforge(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with([
                    assertions.Match.object_like({
                        "Action": "ssm:GetParameter",
                        "Effect": "Allow",
                    })
                ])
            }
        },
    )


def test_iam_s3_policy_scoped_to_bucket(template: assertions.Template) -> None:
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with([
                    assertions.Match.object_like({
                        "Action": assertions.Match.array_with(["s3:PutObject", "s3:GetObject"]),
                        "Effect": "Allow",
                    })
                ])
            }
        },
    )
