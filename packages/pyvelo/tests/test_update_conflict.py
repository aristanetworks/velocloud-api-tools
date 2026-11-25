"""A stale module version, as the VCO answers it, and the module row it returns.

A VCO with its conditional check on refuses an update carrying a version other
than the module's current one before it writes anything, with the JSON-RPC
``validationError`` ``UPDATE_CONFIGURATION_MODULE_PRECONDITION_FAILED``. With the
check off, as observed against a live Orchestrator, the update is answered with
an ordinary result, ``{"rows": 0}``, and no error; nothing is written. The
current version answers ``{"rows": 1}`` (also when the data is unchanged) and a
request without a version writes unconditionally. No other JSON-RPC error is a
stale version: an unknown module is a ``validationError`` with another code.
With ``returnData`` the answer is the module row as stored.
"""

import copy
from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client import VcoClient
from pyvelo.client.exceptions import (
    PRECONDITION_FAILED,
    ConfigConflictError,
    VcoHttpError,
    VcoOperationError,
    is_version_conflict,
)
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    FirewallConfigurationModule,
    InsertConfigurationModuleResult,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.testing import ModuleStore, make_module_json

from ._type_support import JsonObject, JsonRpcResponseFactory, MockVcoClientFactory

UNKNOWN_MODULE_ERROR: JsonObject = {
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

RAW: JsonObject = {"segments": [], "firewall_enabled": True, "future": 1}
"""Module 7 as stored; ``future`` is a key the models do not know."""
EDITED: JsonObject = {"segments": [], "firewall_enabled": False, "future": 1}


def req(
    version: str | None,
    *,
    return_data: bool = False,
    data: JsonObject | None = None,
) -> UpdateConfigurationModule:
    return UpdateConfigurationModule(
        configuration_module_id=7,
        version=version if version else MISSING,
        return_data=True if return_data else MISSING,
        update=ConfigurationModuleUpdate(
            data=copy.deepcopy(data) if data is not None else {"segments": []}
        ),
    )


def qos_insert(*, return_data: bool = False) -> InsertQosConfigurationModule:
    return InsertQosConfigurationModule(
        configuration_id=10,
        data={"segments": []},
        return_data=True if return_data else MISSING,
    )


def firewall_store(**knobs: bool) -> ModuleStore:
    """A store holding firewall module 7, at version ``"1700"``, of edge configuration 10."""
    store = ModuleStore(**knobs)
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    store.rows[7] = make_module_json(
        "firewall",
        copy.deepcopy(RAW),
        module_id=7,
        configuration_id=10,
        version="1700",
    )
    return store


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
def test_is_version_conflict(
    error: str | None, rows: int | None, expected: bool
) -> None:
    assert is_version_conflict(error=error, rows=rows) is expected


def test_return_data_is_sent_as_returnData() -> None:
    assert req("1700", return_data=True).model_dump(by_alias=True)["returnData"] is True
    assert "returnData" not in req("1700").model_dump(by_alias=True)
    assert qos_insert(return_data=True).model_dump(by_alias=True)["returnData"] is True
    assert "returnData" not in qos_insert().model_dump(by_alias=True)


async def test_returned_row_is_a_module_with_its_raw_data_and_level(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = firewall_store()
    client = await mock_vco_client(store)
    module = await client.update_configuration_module(
        req("1700", return_data=True, data=EDITED), level=ConfigContext.EDGE
    )
    assert isinstance(module, FirewallConfigurationModule)
    assert module.raw_data == store.rows[7]["data"] == EDITED
    assert module.raw_mode == "json"
    assert module.data.config_context is ConfigContext.EDGE
    assert module.version == store.rows[7]["version"] != "1700"


@pytest.mark.parametrize("return_data", [True, False])
async def test_precondition_failure_is_a_conflict(
    mock_vco_client: MockVcoClientFactory, return_data: bool
) -> None:
    store = firewall_store(conditional_check=True)
    store.other_writer(7, lambda row: row.update(description="changed elsewhere"))
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.update_configuration_module(
            req("1700", return_data=return_data)
        )
    assert (exc.value.module_id, exc.value.version) == (7, "1700")
    assert exc.value.detail == "the VCO refused the version"
    assert isinstance(exc.value.__cause__, VcoOperationError)


async def test_zero_rows_with_version_is_a_conflict(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 0}))
    )
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.update_configuration_module(req("1700"))
    assert (exc.value.module_id, exc.value.version) == (7, "1700")
    assert exc.value.detail == "no rows were modified"
    assert str(exc.value) == (
        "Configuration module 7 changed since it was read (version 1700): "
        "no rows were modified"
    )


async def test_zero_rows_without_version_is_not(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 0}))
    )
    assert await client.update_configuration_module(req(None)) is None


@pytest.mark.parametrize("version", ["1700", None])
async def test_one_row_is_a_write(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
    version: str | None,
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response({"rows": 1}))
    )
    assert await client.update_configuration_module(req(version)) is None


async def test_result_error_stays_an_operation_error(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response({"error": "validation failed"})
        )
    )
    with pytest.raises(VcoOperationError):
        _ = await client.update_configuration_module(req("1700"))


async def test_result_error_with_zero_rows_is_not_a_conflict(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response({"rows": 0, "error": "validation failed"})
        )
    )
    with pytest.raises(VcoOperationError):
        _ = await client.update_configuration_module(req("1700"))


@pytest.mark.parametrize("version", ["1700", None])
async def test_unknown_module_is_still_an_operation_error(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
    version: str | None,
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200, json=json_rpc_response(error=UNKNOWN_MODULE_ERROR)
        )
    )
    with pytest.raises(VcoOperationError) as exc:
        _ = await client.update_configuration_module(req(version))
    assert not isinstance(exc.value, ConfigConflictError)


async def test_error_response_mentioning_version_is_not_a_conflict(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    """Only the precondition error is a stale version, whatever another error's text says."""
    client = await mock_vco_client(
        lambda r: httpx.Response(
            200,
            json=json_rpc_response(
                error={"code": -32000, "message": "unsupported schema version"}
            ),
        )
    )
    with pytest.raises(VcoOperationError) as exc:
        _ = await client.update_configuration_module(req("1700"))
    assert not isinstance(exc.value, ConfigConflictError)


async def test_asked_for_data_and_got_a_row_count(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    """A VCO that ignores ``returnData`` answers with a row count, handled as without it."""
    store = firewall_store(return_data=False)
    client = await mock_vco_client(store)
    fresh = await client.update_configuration_module(req("1700", return_data=True))
    assert fresh is None
    with pytest.raises(ConfigConflictError) as stale:
        _ = await client.update_configuration_module(req("1700", return_data=True))
    assert stale.value.detail == "no rows were modified"


@pytest.mark.parametrize(
    ("return_data", "answer"),
    [
        (True, {"name": "imageUpdate", "id": 1}),
        (True, {"name": [], "id": 1}),  # a name that is not text is no module's
        (False, {}),
    ],
)
async def test_a_result_that_is_neither_raises(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
    return_data: bool,
    answer: JsonObject,
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response(answer))
    )
    with pytest.raises(VcoOperationError, match="neither"):
        _ = await client.update_configuration_module(
            req("1700", return_data=return_data)
        )


async def send_update(client: VcoClient) -> object:
    return await client.update_configuration_module(
        req("1700", return_data=True), level=ConfigContext.EDGE
    )


async def send_insert(client: VcoClient) -> object:
    return await client.insert_configuration_module(
        InsertFirewallConfigurationModule(
            configuration_id=10, data={"segments": []}, return_data=True
        ),
        level=ConfigContext.EDGE,
    )


@pytest.mark.parametrize("send", [send_update, send_insert])
async def test_a_known_module_row_that_fails_validation_raises_its_error(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
    send: Callable[[VcoClient], Awaitable[object]],
) -> None:
    """A row the models know by name is validated as a module, as on any read."""
    row = make_module_json(
        "firewall", {"segments": "not a list"}, module_id=7, configuration_id=10
    )
    client = await mock_vco_client(
        lambda r: httpx.Response(200, json=json_rpc_response(row))
    )
    with pytest.raises(ValidationError) as exc:
        _ = await send(client)
    assert [error["loc"][-2:] for error in exc.value.errors()] == [("data", "segments")]


async def test_insert_with_return_data_returns_the_module(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = firewall_store()
    client = await mock_vco_client(store)
    module = await client.insert_configuration_module(
        qos_insert(return_data=True), level=ConfigContext.EDGE
    )
    assert isinstance(module, QosConfigurationModule)
    assert module.id_ == 8
    assert store.rows[8]["name"] == "QOS"
    assert module.is_new is False


async def test_insert_without_it_returns_the_id_as_today(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = firewall_store()
    client = await mock_vco_client(store)
    result = await client.insert_configuration_module(qos_insert())
    assert isinstance(result, InsertConfigurationModuleResult)
    assert result.id_ == 8


PRECONDITION_ERROR: JsonObject = {
    "code": -32003,
    "message": "validationError",
    "data": {
        "valid": False,
        # Long enough that the refusal lies past the part of a body an HTTP error shows.
        "warning": [{"message": "w" * 600, "path": "configuration"}],
        "error": [
            {
                "code": PRECONDITION_FAILED,
                "message": "module 7 has changed",
                "path": "configuration.updateConfigurationModule",
            }
        ],
    },
}


@pytest.mark.parametrize("status", [400, 409, 412])
async def test_precondition_failure_in_an_http_error_is_a_conflict(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
    status: int,
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(
            status, json=json_rpc_response(error=PRECONDITION_ERROR)
        )
    )
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.update_configuration_module(req("1700", return_data=True))
    assert (exc.value.module_id, exc.value.version) == (7, "1700")
    assert exc.value.detail == "the VCO refused the version"
    cause = exc.value.__cause__
    assert isinstance(cause, VcoHttpError)
    assert cause.status_code == status
    assert len(cause.body) == 500
    assert PRECONDITION_FAILED in cause.text


async def test_any_other_http_error_of_an_update_is_an_http_error(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    answers = [
        httpx.Response(400, json=json_rpc_response(error=UNKNOWN_MODULE_ERROR)),
        httpx.Response(502, text="<html>Bad Gateway</html>"),
        httpx.Response(400, json=[1, 2]),
    ]
    for answer in answers:
        client = await mock_vco_client(lambda r, answer=answer: answer)
        with pytest.raises(VcoHttpError):
            _ = await client.update_configuration_module(req("1700"))


async def test_the_same_body_from_another_operation_is_an_http_error(
    mock_vco_client: MockVcoClientFactory, json_rpc_response: JsonRpcResponseFactory
) -> None:
    client = await mock_vco_client(
        lambda r: httpx.Response(412, json=json_rpc_response(error=PRECONDITION_ERROR))
    )
    with pytest.raises(VcoHttpError):
        _ = await client.insert_configuration_module(qos_insert())
