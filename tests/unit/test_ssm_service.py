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


def test_bootstrap_creates_new_parameters(ssm):
    result = ssm.bootstrap_agent_ids({"/specforge/agent/quality_id": "PLACEHOLDER"})
    assert result == {"/specforge/agent/quality_id": "created"}
    assert ssm.get_parameter("/specforge/agent/quality_id") == "PLACEHOLDER"


def test_bootstrap_skips_existing_when_not_overwriting(ssm):
    ssm.put_parameter("/specforge/agent/quality_id", "real-id")
    result = ssm.bootstrap_agent_ids({"/specforge/agent/quality_id": "PLACEHOLDER"})
    assert result == {"/specforge/agent/quality_id": "skipped"}
    assert ssm.get_parameter("/specforge/agent/quality_id") == "real-id"


def test_bootstrap_overwrites_existing_when_overwrite_true(ssm):
    ssm.put_parameter("/specforge/agent/quality_id", "old-id")
    result = ssm.bootstrap_agent_ids(
        {"/specforge/agent/quality_id": "new-id"},
        overwrite=True,
    )
    assert result == {"/specforge/agent/quality_id": "overwritten"}
    assert ssm.get_parameter("/specforge/agent/quality_id") == "new-id"


def test_bootstrap_rejects_invalid_parameter_name(ssm):
    with pytest.raises(SSMError, match="/specforge/"):
        ssm.bootstrap_agent_ids({"no-prefix": "PLACEHOLDER"})
    # Nothing should have been written.
    with pytest.raises(SSMError):
        ssm.get_parameter("no-prefix")


def test_bootstrap_returns_status_entry_per_input(ssm):
    agent_map = {
        "/specforge/agent/quality_id": "PLACEHOLDER",
        "/specforge/agent/ambiguity_id": "PLACEHOLDER",
        "/specforge/agent/complexity_id": "PLACEHOLDER",
    }
    result = ssm.bootstrap_agent_ids(agent_map)
    assert set(result.keys()) == set(agent_map.keys())
    assert all(state == "created" for state in result.values())


def test_bootstrap_mixed_existing_and_new(ssm):
    ssm.put_parameter("/specforge/agent/quality_id", "already-set")
    result = ssm.bootstrap_agent_ids(
        {
            "/specforge/agent/quality_id": "PLACEHOLDER",
            "/specforge/agent/ambiguity_id": "PLACEHOLDER",
        }
    )
    assert result["/specforge/agent/quality_id"] == "skipped"
    assert result["/specforge/agent/ambiguity_id"] == "created"
    assert ssm.get_parameter("/specforge/agent/quality_id") == "already-set"
    assert ssm.get_parameter("/specforge/agent/ambiguity_id") == "PLACEHOLDER"
