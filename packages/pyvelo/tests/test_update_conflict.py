import httpx
import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import (
    ConfigConflictError,
    VcoOperationError,
    is_version_conflict,
)
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    UpdateConfigurationModule,
)


def req(version: str | None) -> UpdateConfigurationModule:
    return UpdateConfigurationModule(
        configuration_module_id=7,
        version=version if version else MISSING,
        update=ConfigurationModuleUpdate(data={"segments": []}),
    )


@pytest.mark.parametrize(
    ("error", "rows", "expected"),
    [
        (None, 0, True),
        ("module version mismatch", None, True),
        ("Version conflict", 1, True),
        (None, 1, False),
        ("validation failed: segments", None, False),
        ("address conflict", None, False),
        ("overlapping subnets conflict with an existing network", 1, False),
    ],
)
def test_is_version_conflict(error: str | None, rows: int | None, expected: bool):
    assert is_version_conflict(error=error, rows=rows) is expected


async def test_zero_rows_with_version_is_a_conflict(mock_vco_client, json_rpc_response):
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 0}))
    )
    with pytest.raises(ConfigConflictError) as exc:
        await client.update_configuration_module(req("1700"))
    assert (exc.value.module_id, exc.value.version) == (7, "1700")


async def test_zero_rows_without_version_is_not(mock_vco_client, json_rpc_response):
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 0}))
    )
    await client.update_configuration_module(req(None))


async def test_other_errors_stay_operation_errors(mock_vco_client, json_rpc_response):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response({"error": "validation failed"})
        )
    )
    with pytest.raises(VcoOperationError):
        await client.update_configuration_module(req("1700"))


async def test_stale_version_error_response_is_a_conflict(
    mock_vco_client, json_rpc_response
):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200,
            json=json_rpc_response(
                error={"code": -32000, "message": "module version mismatch"}
            ),
        )
    )
    with pytest.raises(ConfigConflictError) as exc:
        await client.update_configuration_module(req("1700"))
    assert (exc.value.module_id, exc.value.version) == (7, "1700")
    assert exc.value.detail == "module version mismatch"


@pytest.mark.parametrize(
    ("version", "message"),
    [("1700", "validation failed"), (None, "module version mismatch")],
)
async def test_other_error_responses_stay_operation_errors(
    mock_vco_client, json_rpc_response, version: str | None, message: str
):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response(error={"code": -32000, "message": message})
        )
    )
    with pytest.raises(VcoOperationError):
        await client.update_configuration_module(req(version))
