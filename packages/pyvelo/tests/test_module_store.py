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
from pyvelo.core.layers import Edge
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
GET_MODULES = "configuration/getConfigurationModules"
GET_SERVICES = "enterprise/getEnterpriseServices"

DNS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DNS_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
GROUP = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SEG0 = "11111111-1111-4111-8111-111111111111"
PRIMARY = "deviceSettings:dns:primaryProvider"
BACKUP = "deviceSettings:dns:backupProvider"
ADDRESS_GROUP = "objectGroup:addressGroup"
SEGMENT_REF = "deviceSettings:segment"

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


def ref_row(module_id: int, object_id: int, ref: str, **extra: Any) -> dict[str, Any]:
    """A ref row as a plan sends it, for a module of configuration 10."""
    return {
        "configurationId": 10,
        "moduleId": module_id,
        "enterpriseObjectId": object_id,
        "ref": ref,
        **extra,
    }


def update_refs(store: ModuleStore, module_id: int, refs: object) -> dict[str, Any]:
    """Send *refs* with the module's data as stored; the row the store answers."""
    data = copy.deepcopy(store.rows[module_id]["data"])
    params = update(
        data, version=None, refs=refs, module_id=module_id, return_data=True
    )
    return cast("dict[str, Any]", store.handle(UPDATE, params))


def refs_store() -> ModuleStore:
    """Configuration 10 with device settings module 10 of one segment, and the objects 701 (a DNS service) and 501 (the global segment)."""
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    store.rows[10] = make_module_json(
        "deviceSettings",
        {"segments": [{"name": "Global Segment"}]},
        module_id=10,
        configuration_id=10,
    )
    store.add_object(701, logical_id=DNS_A, name="dns-a", type_="dnsService")
    store.add_object(
        501,
        logical_id=SEG0,
        name="Global Segment",
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
    )
    return store


def change_elsewhere(row: dict[str, Any]) -> None:
    row["data"]["syslog_forwarding"] = True


def error_code(error: RpcError) -> object:
    """The ``code`` of the first entry of a ``validationError``."""
    data = cast("dict[str, list[dict[str, object]]]", error.data)
    return data["error"][0]["code"]


def test_update_stores_and_answers_rows() -> None:
    store = make_store()
    assert store.handle(UPDATE, update(EDITED)) == {"rows": 1}
    assert store.rows[1]["data"] == EDITED
    assert store.rows[1]["version"] != "v1"


def test_update_with_return_data_answers_the_row() -> None:
    store = make_store()
    answer = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert answer == store.rows[1]
    assert {"id", "name", "version", "data"} <= set(store.rows[1])


def test_stale_version_with_the_check_off() -> None:
    store = make_store()
    store.other_writer(1, change_elsewhere)
    current = copy.deepcopy(store.rows[1])
    assert store.handle(UPDATE, update(EDITED)) == {"rows": 0}
    assert store.handle(UPDATE, update(EDITED, return_data=True)) == current
    assert store.rows[1] == current


def test_stale_version_with_the_check_on() -> None:
    store = make_store(conditional_check=True)
    store.other_writer(1, change_elsewhere)
    current = copy.deepcopy(store.rows[1])
    with pytest.raises(RpcError) as refused:
        _ = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert refused.value.code == -32003
    assert refused.value.message == "validationError"
    assert error_code(refused.value) == PRECONDITION_FAILED
    assert store.rows[1] == current


def test_no_version_writes_unconditionally() -> None:
    store = make_store(conditional_check=True)
    store.other_writer(1, change_elsewhere)
    assert store.handle(UPDATE, update(EDITED, version=None)) == {"rows": 1}
    assert store.rows[1]["data"] == EDITED


def test_refs_are_replaced_only_when_sent() -> None:
    store = make_store()
    _ = store.handle(UPDATE, update(RAW, version=None))
    assert store.rows[1]["refs"] == REFS
    other = {SEGMENT_REF: [ref_row(1, 6, SEGMENT_REF)]}
    _ = store.handle(UPDATE, update(RAW, version=None, refs=other))
    stored = store.rows[1]["refs"][SEGMENT_REF]
    assert (stored["moduleId"], stored["enterpriseObjectId"]) == (1, 6)
    _ = store.handle(UPDATE, update(RAW, version=None, refs={}))
    assert "refs" not in store.rows[1]
    _ = store.handle(UPDATE, update(RAW, version=None, refs=other))
    _ = store.handle(UPDATE, update(RAW, version=None, refs=[]))
    assert "refs" not in store.rows[1]


def test_hooks_change_what_is_stored() -> None:
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


def test_fail_writes() -> None:
    store = make_store()
    store.fail_writes = {2}
    _ = store.handle(INSERT, insert({"segments": []}))  # writes count together
    with pytest.raises(RpcError) as failed:
        _ = store.handle(UPDATE, update({"n": 2}, version=None))
    assert failed.value.code == -32603
    assert store.rows[1]["data"] == RAW
    _ = store.handle(UPDATE, update({"n": 3}, version=None))
    assert store.rows[1]["data"] == {"n": 3}


def test_return_data_can_be_ignored() -> None:
    store = make_store(return_data=False)
    answer = store.handle(UPDATE, update({"segments": []}, return_data=True))
    assert answer == {"rows": 1}


def test_insert_issues_an_id_and_a_version() -> None:
    store = make_store()
    data: dict[str, Any] = {"segments": [], "rules": []}
    assert store.handle(INSERT, insert(data)) == {"id": 2, "rows": 1}
    row = store.rows[2]
    assert (row["configurationId"], row["name"], row["data"]) == (10, "QOS", data)
    assert row["version"] not in (None, "v1")
    answer = store.handle(INSERT, insert(data, return_data=True))
    assert answer == store.rows[3]
    assert store.rows[3]["version"] != row["version"]


def test_get_configuration_serves_its_modules() -> None:
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


def test_unknown_module_and_unknown_method() -> None:
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


async def test_as_an_httpx_handler(mock_vco_client: MockVcoClientFactory) -> None:
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


def test_store_of_holds_the_rows_and_their_configurations() -> None:
    m1 = (
        FirewallConfigurationModule[Edge]
        .model_validate(
            make_module_json("firewall", RAW, module_id=1, configuration_id=10)
        )
        .for_edge()
    )
    m2 = QosConfigurationModule[Edge].model_validate(
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


def test_as_stored_is_the_module_validated_from_json() -> None:
    module = (
        FirewallConfigurationModule[Edge]
        .model_validate(
            make_module_json("firewall", copy.deepcopy(RAW), module_id=1, version="v1")
        )
        .for_edge()
    )
    data: dict[str, Any] = {**EDITED, "vendorKey": 1}
    stored = as_stored(module, data)
    assert type(stored) is FirewallConfigurationModule[Edge]
    assert stored.config_context is ConfigContext.EDGE
    assert stored.data.config_context is ConfigContext.EDGE
    assert stored.raw_mode == "json"
    assert stored.raw_data == data
    assert stored.version == "v2"
    assert (module.version, module.raw_data) == ("v1", RAW)
    assert module.data.firewall_enabled.local is True


def test_a_store_of_one_enterprise_refuses_a_request_that_does_not_name_it() -> None:
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


def test_a_store_of_no_enterprise_takes_any_request() -> None:
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
    assert store.handle(GET_CONFIGURATION, {"id": 10, "enterpriseId": 8}) == {
        "id": 10,
        "name": "Edge Specific Profile",
    }


async def test_lose_answers_stores_the_write_and_then_times_out(
    mock_vco_client: MockVcoClientFactory,
) -> None:
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
) -> None:
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


def test_the_store_serves_an_edges_stack_from_its_rows() -> None:
    store = stack_store()
    store.add_stack(10, 20, edge_id=42)
    answer = store.handle(STACK, {"edgeId": 42})
    assert answer == [
        {"id": 10, "name": "Edge Specific Profile", "modules": [store.rows[11]]},
        {"id": 20, "name": "Profile 20", "modules": []},
    ]
    store.rows[11]["data"]["firewall_enabled"] = False
    again = cast("list[dict[str, Any]]", store.handle(STACK, {"edgeId": 42}))
    assert again[0]["modules"][0]["data"]["firewall_enabled"] is False
    with pytest.raises(RpcError) as unknown:
        _ = store.handle(STACK, {"edgeId": 7})
    assert unknown.value.code == -32003
    store.add_stack(10, 20)  # for every edge
    assert store.handle(STACK, {"edgeId": 7}) == again


def test_a_module_given_under_another_configuration_is_refused() -> None:
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


def test_a_module_id_held_by_another_configuration_is_refused_and_nothing_changes() -> (
    None
):
    store = ModuleStore()
    first = make_module_json("firewall", {}, module_id=1, configuration_id=10)
    store.add_configuration({"id": 10, "name": "P", "modules": [first]})
    clash = make_module_json("qos", {}, module_id=1, configuration_id=20)
    with pytest.raises(ValueError) as exc:
        store.add_configuration({"id": 20, "name": "Q", "modules": [clash]})
    message = str(exc.value)
    assert "module 1" in message
    assert "configuration 20" in message
    assert "configuration 10 already holds a module with that id" in message
    assert store.rows[1]["name"] == "firewall"
    assert store.rows[1]["configurationId"] == 10
    assert list(store.configurations) == [10]
    # The same configuration given again replaces its own modules.
    store.add_configuration({"id": 10, "name": "P", "modules": [first]})


def test_a_module_row_without_an_id_is_refused_and_nothing_is_stored() -> None:
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
) -> None:
    store = stack_store()
    with pytest.raises(RpcError) as exc:
        _ = store.handle(method, params)
    assert exc.value.code == -32003
    assert f"missing required property: {missing}" in str(exc.value.data)
    assert set(store.rows) == {11}


def test_a_hook_cannot_change_the_stored_row_through_old() -> None:
    store = stack_store()

    def hook(_row: dict[str, Any], old: dict[str, Any] | None) -> None:
        assert old is not None
        old["data"]["touched"] = True

    store.on_store.append(hook)
    kept = store.rows[11]
    _ = store.handle(UPDATE, {"id": 11, "_update": {"data": {"segments": []}}})
    assert "touched" not in kept["data"]


def test_the_store_issues_firewall_rule_ids_as_a_vco_does() -> None:
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


def test_the_store_issues_a_qos_rule_id_only_where_there_is_none() -> None:
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
) -> None:
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


def test_a_read_without_its_key_is_answered_with_an_error() -> None:
    store = stack_store()
    store.add_stack(10, 20)
    for method, missing in ((GET_CONFIGURATION, "id"), (STACK, "edgeId")):
        with pytest.raises(RpcError) as exc:
            _ = store.handle(method, {})
        assert exc.value.code == -32003
        assert f"missing required property: {missing}" in str(exc.value.data)


def test_a_refused_configuration_stores_nothing_and_says_what_to_do() -> None:
    store = ModuleStore()
    good = make_module_json("firewall", {}, module_id=1, configuration_id=10)
    bad = make_module_json("qos", {}, module_id=2, configuration_id=99)
    with pytest.raises(ValueError) as exc:
        store.add_configuration({"id": 10, "name": "P", "modules": [good, bad]})
    assert "give the module under configuration 99" in str(exc.value)
    assert "drop its configurationId so it takes 10" in str(exc.value)
    assert not store.rows
    assert not store.configurations


def test_a_new_row_is_completed_from_its_object() -> None:
    store = refs_store()
    row = ref_row(10, 701, PRIMARY, segmentObjectId=501)
    stored = update_refs(store, 10, {PRIMARY: [row]})
    got = stored["refs"][PRIMARY]
    assert got["logicalId"] == DNS_A
    assert got["name"] == "dns-a"
    assert (got["type"], got["object"]) == ("dnsService", "NETWORK_SERVICE")
    assert got["data"] == {}
    assert got["segmentLogicalId"] == SEG0
    assert isinstance(got["id"], int)
    assert got["configurationId"] == 10
    assert got["segmentObjectId"] == 501
    assert store.rows[10]["refs"] == stored["refs"]


def test_a_row_of_an_unknown_object_is_stored_as_sent_with_an_id() -> None:
    store = refs_store()
    row = ref_row(10, 999, PRIMARY)
    got = update_refs(store, 10, {PRIMARY: [row]})["refs"][PRIMARY]
    assert {k: v for k, v in got.items() if k != "id"} == row
    assert isinstance(got["id"], int)


def test_a_row_without_a_required_id_is_dropped() -> None:
    store = refs_store()
    whole = ref_row(10, 701, PRIMARY)
    for key in ("configurationId", "moduleId", "enterpriseObjectId"):
        refs = {
            PRIMARY: [
                {**whole, key: None},
                {k: v for k, v in whole.items() if k != key},
            ]
        }
        assert "refs" not in update_refs(store, 10, refs)


def test_rows_group_under_their_ref_one_as_an_object_several_as_a_list() -> None:
    store = refs_store()
    store.add_object(702, logical_id=DNS_B, name="dns-b", type_="dnsService")
    refs = {
        "ignored": [
            ref_row(10, 701, PRIMARY),
            ref_row(10, 702, BACKUP),
            ref_row(10, 701, SEGMENT_REF, segmentObjectId=501),
            ref_row(10, 702, SEGMENT_REF, segmentObjectId=501),
            {**ref_row(10, 701, PRIMARY), "ref": None},
        ]
    }
    got = update_refs(store, 10, refs)["refs"]
    assert set(got) == {PRIMARY, BACKUP, SEGMENT_REF, "null"}
    assert got[PRIMARY]["name"] == "dns-a"
    assert [r["name"] for r in got[SEGMENT_REF]] == ["dns-a", "dns-b"]
    assert got["null"]["enterpriseObjectId"] == 701


def test_a_row_with_no_ref_of_an_unknown_object_is_grouped_under_null() -> None:
    store = refs_store()
    row = ref_row(10, 999, PRIMARY)
    del row["ref"]  # a row with no ref key, of an object the store does not know
    got = update_refs(store, 10, {"ignored": [row]})["refs"]
    assert list(got) == ["null"]
    assert got["null"]["enterpriseObjectId"] == 999
    assert "ref" not in got["null"]


def test_a_row_that_fails_is_skipped_and_the_others_are_stored() -> None:
    store = refs_store()
    store.add_object(702, logical_id=DNS_B, name="dns-b", type_="dnsService")
    store.skipped_objects.add(701)
    refs = {PRIMARY: [ref_row(10, 701, PRIMARY), ref_row(10, 702, BACKUP)]}
    got = update_refs(store, 10, refs)["refs"]
    assert list(got) == [BACKUP]
    assert got[BACKUP]["enterpriseObjectId"] == 702


def test_an_insert_sets_the_module_id_of_the_rows_it_is_given() -> None:
    store = refs_store()
    row = ref_row(10, 701, PRIMARY)
    del row["moduleId"]
    params = {
        "configurationId": 10,
        "name": "deviceSettings",
        "data": {"segments": []},
        "refs": {PRIMARY: [row]},
        "returnData": True,
    }
    answer = cast("dict[str, Any]", store.handle(INSERT, params))
    assert answer["refs"][PRIMARY]["moduleId"] == answer["id"] == 11
    assert answer["refs"][PRIMARY]["logicalId"] == DNS_A


def append_only_store() -> ModuleStore:
    """A refs store whose module 10 holds an address group row and a segment row."""
    store = refs_store()
    store.add_object(801, logical_id=GROUP, name="group", type_="addressGroup")
    store.rows[10]["refs"] = {
        ADDRESS_GROUP: {**ref_row(10, 801, ADDRESS_GROUP), "id": 77},
        SEGMENT_REF: {**ref_row(10, 501, SEGMENT_REF), "id": 78},
    }
    return store


def test_append_only_rows_stay_when_refs_are_replaced() -> None:
    store = append_only_store()
    stored = update_refs(store, 10, {})
    assert list(stored["refs"]) == [ADDRESS_GROUP]
    assert stored["refs"][ADDRESS_GROUP]["id"] == 77
    sent = {PRIMARY: [ref_row(10, 701, PRIMARY)]}
    got = update_refs(store, 10, sent)["refs"]
    assert set(got) == {PRIMARY, ADDRESS_GROUP}
    assert got[ADDRESS_GROUP]["id"] == 77


def test_sent_append_only_rows_are_added_to_the_stored_ones() -> None:
    store = append_only_store()
    sent = {ADDRESS_GROUP: [ref_row(10, 701, ADDRESS_GROUP)]}
    got = update_refs(store, 10, sent)["refs"][ADDRESS_GROUP]
    assert [r["enterpriseObjectId"] for r in got] == [801, 701]
    assert got[0]["id"] == 77


def test_processor_rows_replace_the_refs_when_none_are_sent() -> None:
    store = append_only_store()
    css_site = {"id": 5, "enterpriseObjectId": 9}
    store.processor_refs[10] = {
        "deviceSettings:css:site": css_site,
        "deviceSettings:wssIntegration": [{"id": 6}, {"id": 7}],
    }
    data = {"segments": [{"name": "Global Segment"}], "edited": True}
    _ = store.handle(UPDATE, update(data, version=None, module_id=10))
    refs = store.rows[10]["refs"]
    assert set(refs) == {
        ADDRESS_GROUP,
        "deviceSettings:css:site",
        "deviceSettings:wssIntegration",
    }
    assert refs["deviceSettings:css:site"] == css_site
    assert refs["deviceSettings:wssIntegration"] == [{"id": 6}, {"id": 7}]
    assert refs[ADDRESS_GROUP]["id"] == 77
    refs["deviceSettings:wssIntegration"].clear()  # the store holds its own copy
    assert len(store.processor_refs[10]["deviceSettings:wssIntegration"]) == 2


def test_processor_rows_do_not_apply_when_refs_are_sent_or_segments_are_empty() -> None:
    store = append_only_store()
    store.processor_refs[10] = {"deviceSettings:css:site": {"id": 5}}
    kept = copy.deepcopy(store.rows[10]["refs"])
    _ = store.handle(UPDATE, update({"segments": []}, version=None, module_id=10))
    assert store.rows[10]["refs"] == kept
    sent = {PRIMARY: [ref_row(10, 701, PRIMARY)]}
    _ = store.handle(
        UPDATE, update({"segments": [{}]}, version=None, refs=sent, module_id=10)
    )
    assert set(store.rows[10]["refs"]) == {PRIMARY, ADDRESS_GROUP}
    # A module of another name, and one the processor does not know, are not touched.
    store.rows[10]["name"] = "firewall"
    store.rows[10]["refs"] = copy.deepcopy(kept)
    _ = store.handle(UPDATE, update({"segments": [{}]}, version=None, module_id=10))
    assert store.rows[10]["refs"] == kept
    store.rows[10]["name"] = "QOS"
    del store.processor_refs[10]
    _ = store.handle(UPDATE, update({"segments": [{}]}, version=None, module_id=10))
    assert store.rows[10]["refs"] == kept


def test_processor_rows_apply_to_a_qos_module() -> None:
    store = refs_store()
    store.rows[10]["name"] = "QOS"
    store.processor_refs[10] = {"deviceSettings:css:site": {"id": 5}}
    _ = store.handle(UPDATE, update({"segments": [{}]}, version=None, module_id=10))
    assert store.rows[10]["refs"] == {"deviceSettings:css:site": {"id": 5}}


def test_row_ids_are_new_on_every_replacement() -> None:
    store = refs_store()
    sent = {PRIMARY: [ref_row(10, 701, PRIMARY)]}
    first = update_refs(store, 10, sent)["refs"][PRIMARY]["id"]
    second = update_refs(store, 10, sent)["refs"][PRIMARY]["id"]
    assert first != second


def test_get_configuration_modules_without_data() -> None:
    store = refs_store()
    store.rows[11] = make_module_json("firewall", {}, module_id=11, configuration_id=10)
    store.rows[12] = make_module_json("QOS", {}, module_id=12, configuration_id=20)
    sent = {PRIMARY: [ref_row(10, 701, PRIMARY)]}
    _ = update_refs(store, 10, sent)
    asked = {"configurationId": 10, "noData": True, "modules": ["deviceSettings"]}
    answer = cast("list[dict[str, Any]]", store.handle(GET_MODULES, asked))
    assert [r["name"] for r in answer] == ["deviceSettings"]
    assert "data" not in answer[0]
    assert answer[0]["version"] == store.rows[10]["version"]
    assert answer[0]["refs"] == store.rows[10]["refs"]
    answer[0]["refs"].clear()  # the answer is a copy
    assert store.rows[10]["refs"]
    both = cast(
        "list[dict[str, Any]]",
        store.handle(GET_MODULES, {"configurationId": 10, "modules": []}),
    )
    assert [r["name"] for r in both] == ["deviceSettings", "firewall"]
    assert both[0]["data"] == store.rows[10]["data"]
    every = store.handle(GET_MODULES, {"configurationId": 10})
    assert every == both


def test_get_enterprise_services_lists_the_network_service_objects() -> None:
    store = refs_store()
    store.enterprise_id = 7
    got = store.handle(GET_SERVICES, {"enterpriseId": 7})
    assert got == [
        {
            "id": 701,
            "logicalId": DNS_A,
            "name": "dns-a",
            "type": "dnsService",
            "data": {},
            "created": "2024-01-01T00:00:00.000Z",
            "enterpriseId": 7,
        }
    ]


COMPOSED = "edge/getEdgeConfigurationModules"


def test_the_store_serves_the_modules_it_composes_for_an_edge() -> None:
    store = ModuleStore()
    store.composed[42] = {"firewall": copy.deepcopy(RULES), "QOS": None}
    answer = cast("dict[str, Any]", store.handle(COMPOSED, {"edgeId": 42}))
    assert set(answer) == {"firewall", "QOS"}
    assert answer["QOS"] is None  # a module that does not compose is null
    assert answer["firewall"]["data"] == RULES
    assert {"version", "schemaVersion", "type"} <= set(answer["firewall"])
    assert "refs" not in answer["firewall"]

    only = cast(
        "dict[str, Any]", store.handle(COMPOSED, {"edgeId": 42, "modules": ["QOS"]})
    )
    assert set(only) == {"QOS"}
    answer["firewall"]["data"]["segments"].clear()
    assert store.composed[42]["firewall"] == RULES  # the answer is a copy


def test_an_edge_with_no_composed_modules_is_unknown() -> None:
    store = ModuleStore()
    with pytest.raises(RpcError) as unknown:
        _ = store.handle(COMPOSED, {"edgeId": 7})
    assert unknown.value.code == -32003
    with pytest.raises(RpcError) as missing:
        _ = store.handle(COMPOSED, {})
    assert "edgeId" in str(missing.value.data)


async def test_the_client_reads_the_composed_modules_and_the_store_records_the_request(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    from pyvelo.client.enums.configuration import ConfigurationModuleName
    from pyvelo.client.schemas.edge import GetEdgeConfigurationModules

    store = ModuleStore()
    store.composed[42] = {"firewall": copy.deepcopy(RULES)}
    client = await mock_vco_client(store)
    result = await client.get_edge_configuration_modules(
        GetEdgeConfigurationModules(
            edge_id=42, modules=[ConfigurationModuleName.FIREWALL], encrypt_keys=True
        )
    )
    assert result.firewall is not None
    assert result.firewall is not MISSING
    assert result.firewall.raw_data == RULES
    ((method, params),) = store.requests
    assert method == COMPOSED
    assert params == {"edgeId": 42, "modules": ["firewall"], "encryptKeys": True}
