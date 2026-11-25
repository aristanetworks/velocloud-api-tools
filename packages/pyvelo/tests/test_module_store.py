"""``ModuleStore``: a fake VCO that holds module rows as JSON.

Each test runs on a store with one firewall row (``id`` 1, ``version`` ``"v1"``)
of configuration 10, unless it says otherwise.
"""

import copy
from typing import Any, cast

import httpx
import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import PRECONDITION_FAILED, ConfigConflictError
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleUpdate,
    FirewallConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.testing import make_module_json
from pyvelo.testing.module_store import (
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


def test_a_store_of_one_enterprise_refuses_a_request_that_does_not_name_it():
    store = ModuleStore(enterprise_id=7)
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    store.rows[1] = make_module_json(
        "firewall", {"segments": []}, module_id=1, configuration_id=10, version="v1"
    )
    edited: dict[str, Any] = {"segments": [], "firewall_enabled": False}

    for method, params in (
        (GET_CONFIGURATION, {"id": 10}),
        (UPDATE, update(edited)),
        (UPDATE, {**update(edited), "enterpriseId": 8}),
        (INSERT, insert({"segments": []})),
    ):
        with pytest.raises(RpcError, match="enterprise"):
            _ = store.handle(method, params)
    assert store.rows[1]["data"] == {"segments": []}
    assert list(store.rows) == [1]

    assert store.handle(UPDATE, {**update(edited), "enterpriseId": 7}) == {"rows": 1}
    assert store.rows[1]["data"] == edited


def test_a_store_of_no_enterprise_takes_any_request():
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    assert store.handle(GET_CONFIGURATION, {"id": 10, "enterpriseId": 8}) == {
        "id": 10,
        "name": "Edge Specific Profile",
    }


async def test_lose_answers_stores_the_write_and_then_times_out(
    mock_vco_client: MockVcoClientFactory,
):
    store = make_store()
    store.lose_answers = {1, 3}
    client = await mock_vco_client(store)
    request = UpdateConfigurationModule(
        configuration_module_id=1,
        update=ConfigurationModuleUpdate(data=EDITED),
        version="v1",
    )

    with pytest.raises(httpx.ReadTimeout, match="the answer was lost"):
        _ = await client.update_configuration_module(request)

    assert store.rows[1]["data"] == EDITED
    assert store.rows[1]["version"] != "v1"
    request.version = store.rows[1]["version"]
    _ = await client.update_configuration_module(request)  # write 2 is answered
    assert [method for method, _ in store.requests] == [UPDATE, UPDATE]


async def test_lose_answers_leaves_an_error_answered(
    mock_vco_client: MockVcoClientFactory,
):
    store = make_store(conditional_check=True)
    store.lose_answers = {1}
    client = await mock_vco_client(store)
    request = UpdateConfigurationModule(
        configuration_module_id=1,
        update=ConfigurationModuleUpdate(data=EDITED),
        version="stale",
    )
    with pytest.raises(ConfigConflictError):  # the refusal is the answer
        _ = await client.update_configuration_module(request)
    assert store.rows[1]["data"] == RAW


STACK = "edge/getEdgeConfigurationStack"


def rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "match": {}, "action": {"allow_or_deny": "allow"}, **extra}


SEGMENT: dict[str, object] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
}
RULES: dict[str, Any] = {
    "firewall_enabled": True,
    "segments": [
        {
            "segment": SEGMENT,
            "outbound": [rule("allow-web", ruleLogicalId="read-id-0000000000001")],
        }
    ],
}


def stack_store() -> ModuleStore:
    """Configuration 10 with firewall module 11, and an empty configuration 20."""
    store = ModuleStore()
    store.add_configuration(
        {
            "id": 10,
            "name": "Edge Specific Profile",
            "modules": [
                make_module_json(
                    "firewall", copy.deepcopy(RULES), module_id=11, configuration_id=10
                )
            ],
        }
    )
    store.add_configuration({"id": 20, "name": "Profile 20"})
    return store


def test_the_store_serves_an_edges_stack_from_its_rows():
    store = stack_store()
    store.add_stack(10, 20, edge_id=42)
    answer = store.handle(STACK, {"edgeId": 42})
    assert answer == [
        {"id": 10, "name": "Edge Specific Profile", "modules": [store.rows[11]]},
        {"id": 20, "name": "Profile 20", "modules": []},
    ]
    store.rows[11]["data"]["firewall_enabled"] = False
    again = cast(list[dict[str, Any]], store.handle(STACK, {"edgeId": 42}))
    assert again[0]["modules"][0]["data"]["firewall_enabled"] is False
    with pytest.raises(RpcError) as unknown:
        _ = store.handle(STACK, {"edgeId": 7})
    assert unknown.value.code == -32003
    store.add_stack(10, 20)  # for every edge
    assert store.handle(STACK, {"edgeId": 7}) == again


def test_a_module_given_under_another_configuration_is_refused():
    store = ModuleStore()
    row = make_module_json("firewall", {}, module_id=1, configuration_id=99)
    with pytest.raises(
        ValueError,
        match="module 1 is given under configuration 10, but its configurationId is 99",
    ):
        store.add_configuration({"id": 10, "name": "P", "modules": [row]})
    bare = {k: v for k, v in row.items() if k != "configurationId"}
    store.add_configuration({"id": 10, "name": "P", "modules": [bare]})
    assert store.rows[1]["configurationId"] == 10
    with pytest.raises(ValueError, match="a configuration row needs an id"):
        store.add_configuration({"name": "P"})


def test_a_module_row_without_an_id_is_refused_and_nothing_is_stored():
    store = ModuleStore()
    good = make_module_json("firewall", {}, module_id=1, configuration_id=10)
    nameless = {k: v for k, v in good.items() if k != "id"}
    with pytest.raises(ValueError, match="a module row needs an id"):
        store.add_configuration({"id": 10, "name": "P", "modules": [good, nameless]})
    assert (store.configurations, store.rows) == ({}, {})


@pytest.mark.parametrize(
    ("method", "params", "missing"),
    [
        (INSERT, {"configurationId": 10, "data": {}}, "name"),
        (INSERT, {"configurationId": 10, "name": "QOS"}, "data"),
        (INSERT, {"name": "QOS", "data": {}}, "configurationId"),
        (UPDATE, {"_update": {"data": {}}}, "id"),
        (UPDATE, {"id": 11}, "_update"),
        (UPDATE, {"id": 11, "_update": {}}, "data"),
    ],
)
def test_a_request_without_a_required_key_is_answered_with_an_error(
    method: str, params: dict[str, Any], missing: str
):
    store = stack_store()
    with pytest.raises(RpcError) as exc:
        _ = store.handle(method, params)
    assert exc.value.code == -32003
    assert f"missing required property: {missing}" in str(exc.value.data)
    assert set(store.rows) == {11}


def test_a_hook_cannot_change_the_stored_row_through_old():
    store = stack_store()

    def hook(row: dict[str, Any], old: dict[str, Any] | None) -> None:
        assert old is not None
        old["data"]["touched"] = True

    store.on_store.append(hook)
    kept = store.rows[11]
    _ = store.handle(UPDATE, {"id": 11, "_update": {"data": {"segments": []}}})
    assert "touched" not in kept["data"]


def test_the_store_issues_firewall_rule_ids_as_a_vco_does():
    store = stack_store()
    store.issue_rule_ids = True
    data = copy.deepcopy(RULES)
    data["segments"][0]["outbound"].insert(0, rule("block-ssh"))
    _ = store.handle(UPDATE, {"id": 11, "_update": {"data": data}})
    stored = store.rows[11]["data"]["segments"][0]["outbound"]
    assert [(r["name"], r["ruleLogicalId"]) for r in stored] == [
        ("block-ssh", "rule-id-00000000000001"),
        ("allow-web", "read-id-0000000000001"),
    ]
    # A rename keeps the ID; a change of content replaces it.
    edited = copy.deepcopy(store.rows[11]["data"])
    edited["segments"][0]["outbound"][0]["name"] = "deny-ssh"
    edited["segments"][0]["outbound"][1]["loggingEnabled"] = True
    _ = store.handle(UPDATE, {"id": 11, "_update": {"data": edited}})
    stored = store.rows[11]["data"]["segments"][0]["outbound"]
    assert [(r["name"], r["ruleLogicalId"]) for r in stored] == [
        ("deny-ssh", "rule-id-00000000000001"),
        ("allow-web", "rule-id-00000000000002"),
    ]
    assert all(len(r["ruleLogicalId"]) == 22 for r in stored)


def test_the_store_issues_a_qos_rule_id_only_where_there_is_none():
    store = ModuleStore(issue_rule_ids=True)
    store.add_configuration({"id": 10, "name": "P"})
    data: dict[str, Any] = {
        "segments": [
            {
                "segment": SEGMENT,
                "rules": [{"name": "a"}, {"name": "b", "ruleLogicalId": "kept"}],
                "defaults": [{"name": "d"}],
            }
        ]
    }
    _ = store.handle(INSERT, {"configurationId": 10, "name": "QOS", "data": data})
    segment = store.rows[1]["data"]["segments"][0]
    assert [r["ruleLogicalId"] for r in segment["rules"]] == [
        "00000000-0000-0000-0000-000000000001",
        "kept",
    ]
    assert segment["defaults"][0]["ruleLogicalId"] == (
        "00000000-0000-0000-0000-000000000002"
    )


@pytest.mark.parametrize(
    ("method", "params"),
    [
        (INSERT, {"configurationId": 10, "data": {}}),
        (INSERT, {"configurationId": 10, "name": "QOS"}),
        (INSERT, {"name": "QOS", "data": {}}),
        (UPDATE, {"_update": {"data": {}}}),
        (UPDATE, {"id": 11}),
        (UPDATE, {"id": 11, "_update": {}}),
    ],
)
def test_a_refused_request_takes_no_write_number_and_changes_nothing(
    method: str, params: dict[str, Any]
):
    store = stack_store()
    store.fail_writes = {1}
    before = copy.deepcopy(store.rows)
    with pytest.raises(RpcError) as refused:
        _ = store.handle(method, params)
    assert refused.value.code == -32003
    assert store.rows == before
    # The refused request was not write 1: the first real write is.
    with pytest.raises(RpcError) as failed:
        _ = store.handle(UPDATE, {"id": 11, "_update": {"data": {}}})
    assert failed.value.message == "simulated failure"


def test_a_read_without_its_key_is_answered_with_an_error():
    store = stack_store()
    store.add_stack(10, 20)
    for method, missing in ((GET_CONFIGURATION, "id"), (STACK, "edgeId")):
        with pytest.raises(RpcError) as exc:
            _ = store.handle(method, {})
        assert exc.value.code == -32003
        assert f"missing required property: {missing}" in str(exc.value.data)


def test_a_refused_configuration_stores_nothing_and_says_what_to_do():
    store = ModuleStore()
    good = make_module_json("firewall", {}, module_id=1, configuration_id=10)
    bad = make_module_json("qos", {}, module_id=2, configuration_id=99)
    with pytest.raises(ValueError) as exc:
        store.add_configuration({"id": 10, "name": "P", "modules": [good, bad]})
    assert "give the module under configuration 99" in str(exc.value)
    assert "drop its configurationId so it takes 10" in str(exc.value)
    assert not store.rows
    assert not store.configurations
