import boto3
import pytest
from moto import mock_aws

from services.ssm_service import SSMError, SSMService


@pytest.fixture
def ssm(aws_credentials):
    with mock_aws():
        client = boto3.client("ssm", region_name="us-east-1")
        yield SSMService(client=client)


def test_put_and_get_parameter(ssm):
    ssm.put_parameter("/specforge/test", "secret-value")
    result = ssm.get_parameter("/specforge/test")
    assert result == "secret-value"


def test_get_parameter_not_found_raises_ssm_error(ssm):
    with pytest.raises(SSMError, match="'/specforge/missing'"):
        ssm.get_parameter("/specforge/missing")


def test_put_parameter_overwrite_false_raises_ssm_error(ssm):
    ssm.put_parameter("/specforge/dup", "first")
    with pytest.raises(SSMError, match="'/specforge/dup'"):
        ssm.put_parameter("/specforge/dup", "second", overwrite=False)


def test_put_parameter_overwrite_true_succeeds(ssm):
    ssm.put_parameter("/specforge/update", "v1")
    ssm.put_parameter("/specforge/update", "v2", overwrite=True)
    assert ssm.get_parameter("/specforge/update") == "v2"


def test_injected_client_is_used(aws_credentials):
    with mock_aws():
        client = boto3.client("ssm", region_name="us-east-1")
        svc = SSMService(client=client)
        svc.put_parameter("/specforge/inject", "injected")
        assert svc.get_parameter("/specforge/inject") == "injected"
