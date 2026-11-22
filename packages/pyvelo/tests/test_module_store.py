"""``ModuleStore``: a fake VCO that holds module rows as JSON.

Each test runs on a store with one firewall row (``id`` 1, ``version`` ``"v1"``)
of configuration 10, unless it says otherwise.
"""

import copy
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    FirewallConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.testing import make_module_json
from pyvelo.testing.module_store import (
    PRECONDITION_FAILED,
    ModuleStore,
    RpcError,
    as_stored,
    module_row,
    store_of,
)

from ._type_support import MockVcoClientFactory

UPDATE = "configuration/updateConfigurationModule"
INSERT = "configuration/insertConfigurationModule"
GET_CONFIGURATION = "configuration/getConfiguration"

RAW: dict[str, Any] = {"segments": [], "firewall_enabled": True}
EDITED: dict[str, Any] = {"segments": [], "firewall_enabled": False}
REFS: dict[str, Any] = {"deviceSettings:segment": [{"id": 5, "name": "x"}]}


def make_store(**knobs: bool) -> ModuleStore:
    store = ModuleStore(**knobs)
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    store.rows[1] = make_module_json(
        "firewall",
        copy.deepcopy(RAW),
        module_id=1,
        configuration_id=10,
        version="v1",
        refs=copy.deepcopy(REFS),
    )
    return store


def update(
    data: dict[str, Any],
    *,
    version: str | None = "v1",
    return_data: bool = False,
    refs: object = MISSING,
    module_id: int = 1,
) -> dict[str, Any]:
    """The params of an update of module *module_id*, in wire form."""
    body: dict[str, Any] = {"data": data}
    if refs is not MISSING:
        body["refs"] = refs
    params: dict[str, Any] = {"id": module_id, "_update": body}
    if version is not None:
        params["version"] = version
    if return_data:
        params["returnData"] = True
    return params


def insert(data: dict[str, Any], *, return_data: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {
        "configurationId": 10,
        "name": "QOS",
        "data": data,
        "type": "ENTERPRISE",
        "description": "",
    }
    if return_data:
        params["returnData"] = True
    return params


def change_elsewhere(row: dict[str, Any]) -> None:
    row["data"]["syslog_forwarding"] = True


def error_code(error: RpcError) -> object:
    """The ``code`` of the first entry of a ``validationError``."""
    data = cast(dict[str, list[dict[str, object]]], error.data)
    return data["error"][0]["code"]


def test_update_stores_and_answers_rows():
    store = make_store()
    assert store.handle(UPDATE, update(EDITED)) == {"rows": 1}
    assert store.rows[1]["data"] == EDITED
    assert store.rows[1]["version"] != "v1"


def test_update_with_return_data_answers_the_row():
    store = make_store()
    answer = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert answer == store.rows[1]
    assert {"id", "name", "version", "data"} <= set(store.rows[1])


def test_stale_version_with_the_check_off():
    store = make_store()
    store.other_writer(1, change_elsewhere)
    current = copy.deepcopy(store.rows[1])
    assert store.handle(UPDATE, update(EDITED)) == {"rows": 0}
    assert store.handle(UPDATE, update(EDITED, return_data=True)) == current
    assert store.rows[1] == current


def test_stale_version_with_the_check_on():
    store = make_store(conditional_check=True)
    store.other_writer(1, change_elsewhere)
    current = copy.deepcopy(store.rows[1])
    with pytest.raises(RpcError) as refused:
        _ = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert refused.value.code == -32003
    assert refused.value.message == "validationError"
    assert error_code(refused.value) == PRECONDITION_FAILED
    assert store.rows[1] == current


def test_no_version_writes_unconditionally():
    store = make_store(conditional_check=True)
    store.other_writer(1, change_elsewhere)
    assert store.handle(UPDATE, update(EDITED, version=None)) == {"rows": 1}
    assert store.rows[1]["data"] == EDITED


def test_refs_are_replaced_only_when_sent():
    store = make_store()
    _ = store.handle(UPDATE, update(RAW, version=None))
    assert store.rows[1]["refs"] == REFS
    other = {"deviceSettings:segment": [{"id": 6, "name": "y"}]}
    _ = store.handle(UPDATE, update(RAW, version=None, refs=other))
    assert store.rows[1]["refs"] == other
    _ = store.handle(UPDATE, update(RAW, version=None, refs={}))
    assert "refs" not in store.rows[1]
    _ = store.handle(UPDATE, update(RAW, version=None, refs=other))
    _ = store.handle(UPDATE, update(RAW, version=None, refs=[]))
    assert "refs" not in store.rows[1]


def test_hooks_change_what_is_stored():
    store = make_store()
    before = copy.deepcopy(store.rows[1])
    received: list[dict[str, Any] | None] = []

    def hook(row: dict[str, Any], old: dict[str, Any] | None) -> None:
        received.append(copy.deepcopy(old))
        row["data"]["extra"] = 1

    store.on_store.append(hook)
    answer = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert store.rows[1]["data"] == {"segments": [], "extra": 1}
    assert answer == store.rows[1]
    assert received == [before]


def test_fail_writes():
    store = make_store()
    store.fail_writes = {2}
    _ = store.handle(INSERT, insert({"segments": []}))  # writes count together
    with pytest.raises(RpcError) as failed:
        _ = store.handle(UPDATE, update({"n": 2}, version=None))
    assert failed.value.code == -32603
    assert store.rows[1]["data"] == RAW
    _ = store.handle(UPDATE, update({"n": 3}, version=None))
    assert store.rows[1]["data"] == {"n": 3}


def test_return_data_can_be_ignored():
    store = make_store(return_data=False)
    answer = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert answer == {"rows": 1}


def test_insert_issues_an_id_and_a_version():
    store = make_store()
    data: dict[str, Any] = {"segments": [], "rules": []}
    assert store.handle(INSERT, insert(data)) == {"id": 2, "rows": 1}
    row = store.rows[2]
    assert (row["configurationId"], row["name"], row["data"]) == (10, "QOS", data)
    assert row["version"] not in (None, "v1")
    answer = store.handle(INSERT, insert(data, return_data=True))
    assert answer == store.rows[3]
    assert store.rows[3]["version"] != row["version"]


def test_get_configuration_serves_its_modules():
    store = make_store()
    store.add_configuration({"id": 20, "name": "P"})
    store.rows[2] = make_module_json("firewall", {}, module_id=2, configuration_id=20)
    answer = store.handle(GET_CONFIGURATION, {"id": 10, "with": ["modules"]})
    assert answer == {
        "id": 10,
        "name": "Edge Specific Profile",
        "modules": [store.rows[1]],
    }
    assert store.handle(GET_CONFIGURATION, {"id": 10}) == {
        "id": 10,
        "name": "Edge Specific Profile",
    }


def test_unknown_module_and_unknown_method():
    store = make_store()
    with pytest.raises(RpcError) as unknown_module:
        _ = store.handle(UPDATE, update({}, module_id=7))
    assert unknown_module.value.code == -32003
    assert error_code(unknown_module.value) == "UPDATE_CONFIGURATION_MODULE_0002"
    with pytest.raises(RpcError) as unknown_configuration:
        _ = store.handle(GET_CONFIGURATION, {"id": 99})
    assert unknown_configuration.value.code == -32003
    with pytest.raises(RpcError) as unknown_method:
        _ = store.handle("configuration/cloneEnterpriseTemplate", {})
    assert unknown_method.value.code == -32601


async def test_as_an_httpx_handler(mock_vco_client: MockVcoClientFactory):
    store = make_store()
    client = await mock_vco_client(store)
    result = await client.update_configuration_module(
        UpdateConfigurationModule(
            configuration_module_id=1,
            version="v1",
            update=ConfigurationModuleUpdate(data=EDITED),
        )
    )
    assert result is None
    assert store.rows[1]["data"] == EDITED
    assert store.requests[0][0] == "configuration/updateConfigurationModule"
    assert store.requests[0][1]["version"] == "v1"


def test_store_of_holds_the_rows_and_their_configurations():
    m1 = FirewallConfigurationModule.model_validate(
        make_module_json("firewall", RAW, module_id=1, configuration_id=10)
    ).for_edge()
    m2 = QosConfigurationModule.model_validate(
        make_module_json(
            "QOS", {"segments": []}, module_id=2, configuration_id=20, refs=REFS
        )
    )
    store = store_of(m1, m2, conditional_check=True)
    assert set(store.rows) == {1, 2}
    assert (store.rows[1]["name"], store.rows[1]["data"]) == ("firewall", RAW)
    assert "refs" not in store.rows[1]
    assert (store.rows[2]["name"], store.rows[2]["refs"]) == ("QOS", REFS)
    assert store.rows[1] == module_row(m1)
    assert set(store.configurations) == {10, 20}
    assert store.configurations[10]["name"] == "Edge Specific Profile"
    assert store.conditional_check is True
    assert store.return_data is True


def test_as_stored_is_the_module_validated_from_json():
    module = FirewallConfigurationModule.model_validate(
        make_module_json("firewall", copy.deepcopy(RAW), module_id=1, version="v1")
    ).for_edge()
    data: dict[str, Any] = {**EDITED, "vendorKey": 1}
    stored = as_stored(module, data)
    assert type(stored) is FirewallConfigurationModule
    assert stored.config_context is ConfigContext.EDGE
    assert stored.data.config_context is ConfigContext.EDGE
    assert stored.raw_mode == "json"
    assert stored.raw_data == data
    assert stored.version == "v2"
    assert (module.version, module.raw_data) == ("v1", RAW)
    assert module.data.firewall_enabled is True
