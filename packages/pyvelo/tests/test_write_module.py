"""``write_module``: send a planned module, read it back when the VCO returns no row, check it.

Each test runs a client over a ``ModuleStore`` that holds the module, so the
client's own request building, parsing and error mapping run. Unless a test
says otherwise the module is firewall module 1, at version ``"v1"``, of
configuration 10.
"""

import copy
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    VcoOperationError,
    WriteNotAppliedError,
)
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.module_write import insert_request, plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    new_edge_module,
)
from pyvelo.core import ConfigContext
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import (
    ModuleStore,
    make_jsonrpc_response,
    make_module_json,
    store_of,
)

from ._type_support import MockVcoClientFactory, parse_json_object
from .write_helpers import fw

RAW: dict[str, Any] = {
    "segments": [],
    "inbound": [],
    "firewall_enabled": True,
    "syslog_forwarding": False,
    "future": 1,
}
"""A firewall module's data; ``future`` is a key the models do not know."""

ROW1: dict[str, Any] = {
    "id": 101,
    "enterpriseObjectId": 5,
    "segmentObjectId": 9,
    "logicalId": "a5",
}
ROW2: dict[str, Any] = {"id": 102, "enterpriseObjectId": 6, "logicalId": "a6"}
ROW3: dict[str, Any] = {"id": 103, "enterpriseObjectId": 7, "logicalId": "a7"}

GLOBAL_SEGMENT: dict[str, object] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
}


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    """The path of each entry, sorted: the order within one tuple is not asserted."""
    return sorted(str(x.path) for x in xs)


def methods(store: ModuleStore) -> list[str]:
    """The method of each request the store was sent, without its ``configuration/``."""
    return [method.rsplit("/", 1)[-1] for method, _ in store.requests]


def firewall_disabled() -> FirewallConfigurationModule:
    """The firewall module with one edit: ``firewall_enabled`` off."""
    module = fw(RAW)
    module.data.firewall_enabled = False
    return module


def inbound_rule(name: str) -> dict[str, Any]:
    return {"name": name, "match": {}, "action": {"type": "port_forwarding"}}


def qos(raw: dict[str, Any], *, refs: object = MISSING) -> QosConfigurationModule:
    return QosConfigurationModule.model_validate(
        make_module_json(
            "QOS", copy.deepcopy(raw), configuration_id=10, refs=copy.deepcopy(refs)
        )
    )


def new_qos_with_a_segment() -> QosConfigurationModule:
    """An edge QoS module not inserted yet, with the global segment added."""
    module = new_edge_module(ConfigurationModuleName.QOS, 10)
    assert isinstance(module, QosConfigurationModule)
    module.data.segments.append(QosSegment.model_validate({"segment": GLOBAL_SEGMENT}))
    return module


def elsewhere(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller did not edit."""
    row["data"]["future"] = 2


def reading(
    store: ModuleStore, modules: list[dict[str, Any]]
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that sends each request to *store*, but answers a read of configuration 10 with *modules*."""

    def handler(request: httpx.Request) -> httpx.Response:
        method = parse_json_object(request.content)["method"]
        if method != "configuration/getConfiguration":
            return store(request)
        config = copy.deepcopy(store.configurations[10])
        config["modules"] = copy.deepcopy(modules)
        return httpx.Response(200, json=make_jsonrpc_response(result=config))

    return handler


def stored_module(error: ModuleWriteError) -> FirewallConfigurationModule:
    """The module *error* carries, which must be a firewall module."""
    assert isinstance(error.module, FirewallConfigurationModule)
    return error.module


def assert_stale(error: ConfigConflictError, store: ModuleStore) -> None:
    """*error* is the refusal of ``firewall_disabled`` after ``elsewhere``."""
    assert stored_module(error).version == store.rows[1]["version"]
    assert paths(error.lost_edits) == ["firewall_enabled"]
    assert paths(error.server_changes) == ["future"]


async def test_update_asks_for_data_and_returns_the_stored_module(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    ((_, params),) = store.requests
    assert params["returnData"] is True
    assert params["version"] == "v1"
    assert write.module.version == store.rows[1]["version"] != "v1"
    assert write.module.raw_data == store.rows[1]["data"]
    assert type(write.module) is FirewallConfigurationModule
    assert methods(store) == ["updateConfigurationModule"]


@pytest.mark.parametrize("return_data", [True, False])
async def test_the_stored_module_has_the_level_of_the_one_given(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = fw(RAW).for_edge()
    module.data.firewall_enabled = False
    store = store_of(module, return_data=return_data)
    # Not an edge configuration's name: the read puts the module at profile
    # level, so a module read back is at edge level only as the one given is.
    store.configurations[10]["name"] = "Profile 10"
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert len(store.requests) == (1 if return_data else 2)
    assert write.module.data.config_context is ConfigContext.EDGE


async def test_nothing_to_send_returns_the_module_itself(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw(RAW)
    store = store_of(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert write.module is module
    assert store.requests == []


async def test_keys_the_vco_adds_are_server_changes_and_the_next_baseline(
    mock_vco_client: MockVcoClientFactory,
):
    def add_a_key(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        new["data"]["added"] = 1

    module = firewall_disabled()
    store = store_of(module)
    store.on_store.append(add_a_key)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert paths(write.server_changes) == ["added"]
    assert plan_module(write.module).operation == "none"


async def test_ignored_path_is_not_applied(mock_vco_client: MockVcoClientFactory):
    def keep_syslog_forwarding(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        assert old is not None
        new["data"]["syslog_forwarding"] = old["data"]["syslog_forwarding"]

    module = firewall_disabled()
    module.data.syslog_forwarding = True
    store = store_of(module)
    store.on_store.append(keep_syslog_forwarding)
    client = await mock_vco_client(store)
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert paths(exc.value.lost_edits) == ["syslog_forwarding"]
    assert paths(exc.value.applied) == ["firewall_enabled"]
    assert stored_module(exc.value).version == store.rows[1]["version"]


async def test_stale_version_with_the_check_off(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert methods(store) == ["updateConfigurationModule"]
    assert_stale(exc.value, store)


async def test_stale_version_with_the_check_on_reads_and_raises_the_same(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, conditional_check=True)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert_stale(exc.value, store)
    assert exc.value.detail == "the VCO refused the version"
    refusal = exc.value.__cause__
    assert isinstance(refusal, ConfigConflictError)
    assert isinstance(refusal.__cause__, VcoOperationError)


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_clash_with_another_writer(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    def edit_both(row: dict[str, Any]) -> None:
        row["data"]["future"] = 3
        row["data"]["syslog_forwarding"] = True

    module = fw(RAW)
    plan = plan_module(module, {**RAW, "future": 2})
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(1, edit_both)
    client = await mock_vco_client(store)
    expected = ConfigConflictError if conditional_check else WriteNotAppliedError
    with pytest.raises(ModuleWriteError) as exc:
        _ = await client.write_module(module, plan)
    assert type(exc.value) is expected
    (lost,) = exc.value.lost_edits
    assert lost.clash
    assert (lost.as_read, lost.intended, lost.stored) == (1, 2, 3)
    assert paths(exc.value.server_changes) == ["syslog_forwarding"]


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_refused_write_whose_changes_are_already_stored_passes(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    def same_edit(row: dict[str, Any]) -> None:
        row["data"]["firewall_enabled"] = False

    module = firewall_disabled()
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(1, same_edit)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert paths(write.applied) == ["firewall_enabled"]
    assert write.module.version == store.rows[1]["version"]


async def test_vco_that_ignores_return_data_is_read_back(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, return_data=False)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert store.requests[1][1] == {"id": 10, "with": ["modules"]}
    assert write.module.raw_data == store.rows[1]["data"]


async def test_rows_zero_from_such_a_vco_is_a_conflict(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, return_data=False)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert_stale(exc.value, store)


async def test_follow_up_read_error_propagates(mock_vco_client: MockVcoClientFactory):
    module = firewall_disabled()
    store = store_of(module, conditional_check=True)
    store.other_writer(1, elsewhere)
    del store.configurations[10]
    client = await mock_vco_client(store)
    with pytest.raises(VcoOperationError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert not isinstance(exc.value, ConfigConflictError)
    assert isinstance(exc.value.__context__, ConfigConflictError)


async def test_follow_up_read_without_the_module(mock_vco_client: MockVcoClientFactory):
    module = firewall_disabled()
    store = store_of(module, conditional_check=True)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(reading(store, []))
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.module is None
    assert exc.value.detail == (
        "the VCO refused the version, and its configuration does not hold the module"
    )
    (lost,) = exc.value.lost_edits
    assert (lost.path, lost.as_read, lost.intended) == ("firewall_enabled", True, False)
    assert lost.stored is MISSING


async def test_follow_up_read_without_the_module_lists_the_ref_rows(
    mock_vco_client: MockVcoClientFactory,
):
    module = qos({"segments": []}, refs={"a": [ROW1, ROW3]})
    plan = plan_module(module, refs={"a": [ROW1, ROW2]})
    store = store_of(module, conditional_check=True)
    store.other_writer(module.id_, elsewhere)
    client = await mock_vco_client(reading(store, []))
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.module is None
    assert paths(exc.value.lost_edits) == ["a[6]", "a[7]"]
    added, removed = sorted(exc.value.lost_edits, key=lambda change: str(change.path))
    assert (added.part, added.intended) == ("refs", ROW2)
    assert added.stored is MISSING
    # Nothing was read back, so the refs are taken to be stored as read.
    assert (removed.part, removed.as_read, removed.stored) == ("refs", ROW3, ROW3)
    assert removed.intended is MISSING


async def test_refs_only_write_refused_raises(mock_vco_client: MockVcoClientFactory):
    module = qos({"segments": []}, refs={"a": ROW1})
    plan = plan_module(module, refs={"a": [ROW1, ROW2]})
    store = store_of(module)
    store.other_writer(module.id_, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.lost_edits[0].part == "refs"


async def test_reorder_only_write_refused_raises(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw({**RAW, "inbound": [inbound_rule("a"), inbound_rule("b")]})
    module.data.inbound.reverse()
    store = store_of(module)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    (lost,) = exc.value.lost_edits
    assert lost.path == "inbound"
    assert (lost.as_read, lost.intended, lost.stored) == (
        ["a", "b"],
        ["b", "a"],
        ["a", "b"],
    )


async def test_insert_returns_the_row_and_is_not_new(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert methods(store) == ["insertConfigurationModule"]
    assert write.operation == "insert"
    assert write.module.is_new is False
    (stored_id,) = store.rows
    assert write.module.id_ == stored_id


async def test_insert_answered_without_a_row_is_read_back(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module, return_data=False)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert methods(store) == ["insertConfigurationModule", "getConfiguration"]
    (stored_id,) = store.rows
    assert (write.module.name, write.module.id_) == ("QOS", stored_id)


async def test_insert_is_read_back_by_the_id_answered(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module, return_data=False)
    # The read answers with another writer's module of the same name only.
    another = make_module_json(
        "QOS", {"segments": []}, module_id=99, configuration_id=10
    )
    client = await mock_vco_client(reading(store, [another]))
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    (answered,) = store.rows
    assert exc.value.module_id == answered != 99
    assert exc.value.module is None
    assert exc.value.detail == (
        "the VCO counted the write, but the module could not be read back"
        " (its configuration does not hold the module)"
    )


async def test_a_plan_for_another_module_is_refused(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(store)
    plan = plan_module(qos({"segments": []}), {"segments": [], "x": 1})
    with pytest.raises(ValueError, match="the plan is for QOS, not firewall"):
        _ = await client.write_module(module, plan)
    assert store.requests == []


async def test_a_module_held_as_another_class_raises(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    store.rows[1]["name"] = "QOS"
    client = await mock_vco_client(store)
    with pytest.raises(VcoOperationError, match="as a QosConfigurationModule"):
        _ = await client.write_module(module, plan_module(module))


def test_insert_request_refuses_what_it_cannot_insert():
    module = new_qos_with_a_segment()
    plan = plan_module(module)
    module.configuration_id = None
    with pytest.raises(ValueError, match="configuration ID"):
        _ = insert_request(module, plan, None)
    wan = WanConfigurationModule.model_validate(
        make_module_json("WAN", {"links": []}, configuration_id=10)
    )
    with pytest.raises(TypeError, match="WanConfigurationModule"):
        _ = insert_request(wan, plan_module(wan, {"links": [], "x": 1}), None)


async def test_update_module_returns_the_stored_module(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw(RAW)
    store = store_of(module)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.firewall_enabled = False
    stored = await client.update_module(module, data)
    assert stored.version == store.rows[1]["version"] != "v1"
    assert await client.update_module(module, module.mutable_data()) is module


ITEMS: list[dict[str, Any]] = [{"a": 1, "b": 2}]
"""A list the models do not know, so it is compared whole."""


def syslog_on(row: dict[str, Any]) -> None:
    """Another writer's change to a key no test here edits or asserts on."""
    row["data"]["syslog_forwarding"] = True


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_stale_removal_inside_a_list_item_raises(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = fw({**RAW, "items": ITEMS})
    plan = plan_module(module, {**RAW, "items": [{"a": 1}]})
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(1, syslog_on)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert paths(exc.value.lost_edits) == ["items"]
    assert exc.value.applied == ()
    assert store.rows[1]["data"]["items"] == ITEMS


async def test_stale_removal_beside_another_edit_is_a_lost_edit_too(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw({**RAW, "items": ITEMS})
    plan = plan_module(module, {**RAW, "items": [{"a": 1}], "future": 2})
    store = store_of(module)
    store.other_writer(1, syslog_on)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert paths(exc.value.lost_edits) == ["future", "items"]
    assert exc.value.applied == ()
    assert "already stored" not in str(exc.value)


ECMP: dict[str, Any] = {"maximumPaths": 4, "loadSharing": {"hashParams": {}}}
ECMP_DEFAULT: dict[str, Any] = {"maximumPaths": 2, "loadSharing": {"hashParams": {}}}
DS_RAW: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [{"segment": GLOBAL_SEGMENT, "ecmp": ECMP}],
}
"""Device settings whose global segment has an ``ecmp`` of its own."""
DS_WITHOUT_ECMP: dict[str, Any] = {**DS_RAW, "segments": [{"segment": GLOBAL_SEGMENT}]}
ECMP_PATH = 'segments["Global Segment"].ecmp'


def ds(
    raw: dict[str, Any], *, level: ConfigContext | None = None
) -> DeviceSettingsConfigurationModule:
    """Device settings module 1 of configuration 10, read with *raw* as its data, at *level*."""
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(raw), configuration_id=10),
        context=None if level is None else {"config_level": level},
    )


def other_key(row: dict[str, Any]) -> None:
    """Another writer's change to device settings."""
    row["data"]["otherKey"] = 1


def add_the_default_back(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
    """Store a default ``ecmp`` in each segment sent without one, as the VCO does."""
    for segment in new["data"]["segments"]:
        _ = segment.setdefault("ecmp", copy.deepcopy(ECMP_DEFAULT))


async def test_a_plan_with_nothing_checkable_asks_for_a_count_and_reads_back(
    mock_vco_client: MockVcoClientFactory,
):
    module = ds(DS_RAW)
    plan = plan_module(module, DS_WITHOUT_ECMP)
    store = store_of(module)
    store.on_store.append(add_the_default_back)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert "returnData" not in store.requests[0][1]
    assert store.requests[0][1]["version"] == "v1"
    assert paths(write.left_out) == [ECMP_PATH]
    assert write.applied == ()
    assert write.module.version == store.rows[1]["version"]
    assert write.module.raw_data == store.rows[1]["data"]


async def test_a_ref_row_changed_inside_asks_for_a_count_too(
    mock_vco_client: MockVcoClientFactory,
):
    module = qos({"segments": []}, refs={"a": [ROW1]})
    changed = {"a": [{**ROW1, "logicalId": "b5"}]}
    store = store_of(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module, refs=changed))
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert "returnData" not in store.requests[0][1]
    assert store.rows[1]["refs"] == changed
    assert write.module.raw_refs == changed


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_a_stale_write_with_nothing_checkable_is_a_conflict(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = ds(DS_RAW)
    plan = plan_module(module, DS_WITHOUT_ECMP)
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(1, other_key)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    error = exc.value
    assert error.stated is True
    assert paths(error.lost_edits) == [ECMP_PATH]
    assert error.left_out == ()
    assert paths(error.server_changes) == ["otherKey"]
    assert isinstance(error.module, DeviceSettingsConfigurationModule)
    assert error.module.version == store.rows[1]["version"]
    assert store.rows[1]["data"]["segments"][0]["ecmp"] == ECMP


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_a_stale_write_of_a_ref_row_changed_inside_is_a_conflict(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = qos({"segments": []}, refs={"a": [ROW1]})
    plan = plan_module(module, refs={"a": [{**ROW1, "logicalId": "b5"}]})
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(module.id_, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.stated is True
    assert exc.value.lost_edits == ()
    assert exc.value.plan is plan
    assert store.rows[1]["refs"] == {"a": [ROW1]}


@pytest.mark.parametrize("return_data", [True, False])
async def test_a_module_with_no_level_comes_back_with_none(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = firewall_disabled()
    assert module.config_context is None
    store = store_of(module, return_data=return_data)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert write.module.config_context is None
    assert write.module.data.config_context is None


@pytest.mark.parametrize("return_data", [True, False])
async def test_a_profile_module_comes_back_at_profile_level_at_every_depth(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = ds(DS_RAW, level=ConfigContext.PROFILE)
    plan = plan_module(module, {**DS_RAW, "otherKey": 1})
    store = store_of(module, return_data=return_data)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
    assert write.module.config_context is ConfigContext.PROFILE
    assert write.module.data.config_context is ConfigContext.PROFILE
    assert write.module.data.segments[0].config_context is ConfigContext.PROFILE
    assert write.module.raw_mode == "json"
    assert plan_module(write.module).operation == "none"


async def test_the_read_back_parses_only_the_module_written(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, return_data=False)
    # Another module of the same configuration, which the models do not take.
    store.rows[2] = make_module_json(
        "QOS", {"segments": "not a list"}, module_id=2, configuration_id=10
    )
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module))
    assert write.module.raw_data == store.rows[1]["data"]
