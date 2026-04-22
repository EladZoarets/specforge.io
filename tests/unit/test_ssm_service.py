from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
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


def test_get_parameter_if_exists_returns_value(ssm):
    ssm.put_parameter("/specforge/present", "the-value")
    assert ssm.get_parameter_if_exists("/specforge/present") == "the-value"


def test_get_parameter_if_exists_returns_none_on_parameter_not_found(ssm):
    # Must NOT raise — caller uses None to distinguish "missing" from
    # "SSM broken".
    assert ssm.get_parameter_if_exists("/specforge/nope") is None


def test_get_parameter_if_exists_raises_ssm_error_on_other_client_errors(ssm):
    """Non-ParameterNotFound ClientErrors (AccessDenied, InternalError,
    network issues, etc.) MUST surface as :class:`SSMError` — they're
    reachability errors, not "missing parameter".
    """
    other_error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetParameter",
    )
    with patch.object(ssm._client, "get_parameter", side_effect=other_error):
        with pytest.raises(SSMError, match="/specforge/secret"):
            ssm.get_parameter_if_exists("/specforge/secret")


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


def test_bootstrap_overwrite_true_on_empty_store_reports_created(ssm):
    """overwrite=True must not lie: fresh creates report 'created', not 'overwritten'."""
    agent_map = {
        "/specforge/agent/quality_id": "v1",
        "/specforge/agent/ambiguity_id": "v1",
    }
    result = ssm.bootstrap_agent_ids(agent_map, overwrite=True)
    assert result == {
        "/specforge/agent/quality_id": "created",
        "/specforge/agent/ambiguity_id": "created",
    }


def test_bootstrap_overwrite_true_mixed_reports_accurate_status(ssm):
    """overwrite=True: pre-existing -> 'overwritten', new -> 'created'."""
    ssm.put_parameter("/specforge/agent/quality_id", "old")
    result = ssm.bootstrap_agent_ids(
        {
            "/specforge/agent/quality_id": "new",
            "/specforge/agent/ambiguity_id": "new",
        },
        overwrite=True,
    )
    assert result["/specforge/agent/quality_id"] == "overwritten"
    assert result["/specforge/agent/ambiguity_id"] == "created"
    assert ssm.get_parameter("/specforge/agent/quality_id") == "new"
    assert ssm.get_parameter("/specforge/agent/ambiguity_id") == "new"


def test_bootstrap_handles_check_then_put_race(ssm):
    """Simulate the race: existence check says False, but put_parameter raises
    ParameterAlreadyExists (another writer beat us). Status must be 'skipped',
    never propagate as SSMError.
    """
    race_error = ClientError(
        {"Error": {"Code": "ParameterAlreadyExists", "Message": "race"}},
        "PutParameter",
    )

    with patch.object(ssm, "_parameter_exists", return_value=False), \
         patch.object(ssm._client, "put_parameter", side_effect=race_error):
        result = ssm.bootstrap_agent_ids({"/specforge/agent/quality_id": "v"})

    assert result == {"/specforge/agent/quality_id": "skipped"}


def test_bootstrap_try_create_propagates_other_client_errors(ssm):
    """Non-ParameterAlreadyExists ClientErrors in _try_create still surface as SSMError."""
    other_error = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "boom"}},
        "PutParameter",
    )

    with patch.object(ssm, "_parameter_exists", return_value=False), \
         patch.object(ssm._client, "put_parameter", side_effect=other_error):
        with pytest.raises(SSMError, match="/specforge/agent/quality_id"):
            ssm.bootstrap_agent_ids({"/specforge/agent/quality_id": "v"})
