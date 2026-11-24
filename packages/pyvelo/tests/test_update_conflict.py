"""A stale module version, as the VCO answers it.

Observed against a live Orchestrator, an update carrying a version other than the
module's current one is answered with an ordinary result, ``{"rows": 0}``, and
no error; nothing is written. The current version answers ``{"rows": 1}`` (also
when the data is unchanged) and a request without a version writes
unconditionally. A JSON-RPC error is never a stale version: an unknown module is
a ``validationError``.
"""

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

UNKNOWN_MODULE_ERROR = {
    "code": -32003,
    "message": "validationError",
    "data": {
        "valid": False,
        "error": [
            {
                "code": "UPDATE_CONFIGURATION_MODULE_0002",
                "message": "module not found with ID: 7",
                "path": "configuration.updateConfigurationModule",
            }
        ],
        "warning": [],
    },
}


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
        ("", 0, True),
        (None, 1, False),
        (None, None, False),
        ("validation failed: segments", None, False),
        ("validation failed: segments", 0, False),
        ("module version mismatch", None, False),
        ("Version conflict", 1, False),
        ("address conflict", None, False),
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
    assert exc.value.detail == "no rows were modified"


async def test_zero_rows_without_version_is_not(mock_vco_client, json_rpc_response):
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 0}))
    )
    await client.update_configuration_module(req(None))


@pytest.mark.parametrize("version", ["1700", None])
async def test_one_row_is_a_write(
    mock_vco_client, json_rpc_response, version: str | None
):
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 1}))
    )
    await client.update_configuration_module(req(version))


async def test_result_error_stays_an_operation_error(
    mock_vco_client, json_rpc_response
):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response({"error": "validation failed"})
        )
    )
    with pytest.raises(VcoOperationError):
        await client.update_configuration_module(req("1700"))


async def test_result_error_with_zero_rows_is_not_a_conflict(
    mock_vco_client, json_rpc_response
):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response({"rows": 0, "error": "validation failed"})
        )
    )
    with pytest.raises(VcoOperationError):
        await client.update_configuration_module(req("1700"))


@pytest.mark.parametrize("version", ["1700", None])
async def test_unknown_module_is_an_operation_error(
    mock_vco_client, json_rpc_response, version: str | None
):
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response(error=UNKNOWN_MODULE_ERROR)
        )
    )
    with pytest.raises(VcoOperationError) as exc:
        await client.update_configuration_module(req(version))
    assert not isinstance(exc.value, ConfigConflictError)


async def test_error_response_mentioning_version_is_not_a_conflict(
    mock_vco_client, json_rpc_response
):
    """The VCO does not report a stale version as an error, so no error is one."""
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200,
            json=json_rpc_response(
                error={"code": -32000, "message": "unsupported schema version"}
            ),
        )
    )
    with pytest.raises(VcoOperationError) as exc:
        await client.update_configuration_module(req("1700"))
    assert not isinstance(exc.value, ConfigConflictError)
