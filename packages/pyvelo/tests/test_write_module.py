"""``write_module``: send a planned module, read it back when the VCO returns no row, check it.

Each test runs a client over a ``ModuleStore`` that holds the module, so the
client's own request building, parsing and error mapping run. Unless a test
says otherwise the module is firewall module 1, at version ``"v1"``, of
configuration 10.
"""

import copy
import json
from collections.abc import Callable, Iterable
from typing import Any, cast

import httpx
import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    PRECONDITION_FAILED,
    ConfigConflictError,
    DetachedObjectError,
    ModuleWriteError,
    VcoHttpError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
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
from pyvelo.core.layers import Edge
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import (
    ModuleStore,
    make_jsonrpc_response,
    make_module_json,
    store_of,
)
from refs_helpers import (
    CONFIG,
    DNS_A,
    PRIMARY,
    changing_after_the_version_read,
    copy_of_data,
    primary_provider_module,
    set_primary_provider,
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


def firewall_disabled() -> FirewallConfigurationModule[Edge]:
    """The firewall module with one edit: ``firewall_enabled`` off."""
    module = fw(RAW, layer=Edge)
    module.data.firewall_enabled.set_local(False)
    return module


def inbound_rule(name: str) -> dict[str, Any]:
    return {"name": name, "match": {}, "action": {"type": "port_forwarding"}}


def qos(raw: dict[str, Any], *, refs: object = MISSING) -> QosConfigurationModule[Edge]:
    return QosConfigurationModule[Edge].model_validate(
        make_module_json(
            "QOS", copy.deepcopy(raw), configuration_id=10, refs=copy.deepcopy(refs)
        )
    )


def new_qos_with_a_segment() -> QosConfigurationModule[Edge]:
    """An edge QoS module not inserted yet, with the global segment added."""
    module = new_edge_module(ConfigurationModuleName.QOS, 10)
    assert isinstance(module, QosConfigurationModule)
    module.data.segments.append(
        QosSegment[Edge].model_validate({"segment": GLOBAL_SEGMENT})
    )
    return module


def elsewhere(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller did not edit."""
    row["data"]["future"] = 2


def reading(
    store: ModuleStore, modules: list[dict[str, Any]], configuration_id: int = 10
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that sends each request to *store*, but answers a read of a configuration (10 unless given) with *modules*."""

    def handler(request: httpx.Request) -> httpx.Response:
        method = parse_json_object(request.content)["method"]
        if method != "configuration/getConfiguration":
            return store(request)
        config = copy.deepcopy(store.configurations[configuration_id])
        config["modules"] = copy.deepcopy(modules)
        return httpx.Response(200, json=make_jsonrpc_response(result=config))

    return handler


def stored_module(error: ModuleWriteError) -> FirewallConfigurationModule[Edge]:
    """The module *error* carries, which must be a firewall module."""
    assert isinstance(error.module, FirewallConfigurationModule)
    return cast(FirewallConfigurationModule[Edge], error.module)


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
    assert type(write.module) is FirewallConfigurationModule[Edge]
    assert methods(store) == ["updateConfigurationModule"]


@pytest.mark.parametrize("return_data", [True, False])
async def test_the_stored_module_has_the_level_of_the_one_given(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = fw(RAW, layer=Edge).for_edge()
    module.data.firewall_enabled.set_local(False)
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
    module = fw(RAW, layer=Edge)
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
    module.data.syslog_forwarding.set_local(True)
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

    module = fw(RAW, layer=Edge)
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


@pytest.mark.parametrize(
    ("conditional_check", "raised"),
    [(False, WriteNotAppliedError), (True, ConfigConflictError)],
)
async def test_an_edit_another_writer_made_identically_counts_as_applied(
    mock_vco_client: MockVcoClientFactory,
    conditional_check: bool,
    raised: type[ModuleWriteError],
):
    def same_edit(row: dict[str, Any]) -> None:
        row["data"]["firewall_enabled"] = False

    module = fw(RAW, layer=Edge)
    data = module.mutable_data()
    data.firewall_enabled.set_local(False)
    data.syslog_forwarding.set_local(True)
    store = store_of(module, conditional_check=conditional_check)
    store.other_writer(1, same_edit)
    client = await mock_vco_client(store)
    with pytest.raises(ModuleWriteError) as exc:
        _ = await client.write_module(module, plan_module(module, data))
    assert type(exc.value) is raised
    assert paths(exc.value.applied) == ["firewall_enabled"]
    assert paths(exc.value.lost_edits) == ["syslog_forwarding"]


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


async def test_refs_only_write_meeting_a_changed_version_raises_before_sending(
    mock_vco_client: MockVcoClientFactory,
):
    module = primary_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, None)
    plan = plan_module(module, current)
    assert (plan.operation, plan.changes) == ("update", ())
    store = store_of(module)
    store.other_writer(module.id_, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.lost_edits[0].part == "refs"


async def test_reorder_only_write_refused_raises(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw({**RAW, "inbound": [inbound_rule("a"), inbound_rule("b")]}, layer=Edge)
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
    assert methods(store) == ["insertConfigurationModule", "getConfiguration"]
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
    module = fw(RAW, layer=Edge)
    store = store_of(module)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.firewall_enabled.set_local(False)
    stored = await client.update_module(module, data)
    assert stored.version == store.rows[1]["version"] != "v1"
    # Nothing to write: the module given is returned, and stays usable.
    assert await client.update_module(stored, stored.mutable_data()) is stored
    assert not stored.is_detached


async def test_update_module_refuses_a_module_an_earlier_write_replaced(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw(RAW, layer=Edge)
    store = store_of(module)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.firewall_enabled.set_local(False)
    stored = await client.update_module(module, data)
    assert stored is not module and module.is_detached
    data.syslog_forwarding.set_local(True)
    with pytest.raises(DetachedObjectError) as exc:
        _ = await client.update_module(module, data)
    assert str(exc.value) == (
        "Configuration module 1 was replaced by an earlier write: it holds the data "
        "and the version from before that write. Use the module that write returned "
        "(or error.module, after a write error)."
    )
    assert len(store.requests) == 1
    # An edit through the old module's own data says where the module is now.
    with pytest.raises(DetachedObjectError) as exc:
        module.data.firewall_enabled.set_local(True)
    assert "after update_module: the module it returned" in str(exc.value)
    again = stored.mutable_data()
    again.syslog_forwarding.set_local(True)
    final = await client.update_module(stored, again)
    assert store.rows[1]["data"] == {
        **RAW,
        "firewall_enabled": False,
        "syslog_forwarding": True,
    }
    assert final.version == store.rows[1]["version"]


async def test_update_module_replaces_the_module_after_a_write_error_too(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw(RAW, layer=Edge)
    store = store_of(module)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.firewall_enabled.set_local(False)
    with pytest.raises(ModuleWriteError) as exc:
        _ = await client.update_module(module, data)
    assert exc.value.module is not None and not exc.value.module.is_detached
    assert module.is_detached


async def test_update_module_keeps_the_module_when_the_outcome_is_unknown(
    mock_vco_client: MockVcoClientFactory,
):
    def unreadable(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        new["data"]["segments"] = 5

    module = fw(RAW, layer=Edge)
    store = store_of(module)
    store.on_store.append(unreadable)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.firewall_enabled.set_local(False)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.update_module(module, data)
    assert exc.value.module is None
    assert not module.is_detached


ITEMS: list[dict[str, Any]] = [{"a": 1, "b": 2}]
"""A list the models do not know, so it is compared whole."""


def syslog_on(row: dict[str, Any]) -> None:
    """Another writer's change to a key no test here edits or asserts on."""
    row["data"]["syslog_forwarding"] = True


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_stale_removal_inside_a_list_item_raises(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = fw({**RAW, "items": ITEMS}, layer=Edge)
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
    module = fw({**RAW, "items": ITEMS}, layer=Edge)
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
    # The module has a segment, so the update sends refs and reads the version first.
    assert methods(store) == [
        "getConfigurationModules",
        "updateConfigurationModule",
        "getConfiguration",
    ]
    assert "returnData" not in store.requests[1][1]
    assert store.requests[1][1]["version"] == "v1"
    assert paths(write.left_out) == [ECMP_PATH]
    assert write.applied == ()
    assert write.module.version == store.rows[1]["version"]
    assert write.module.raw_data == store.rows[1]["data"]


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_a_stale_write_with_nothing_checkable_is_a_conflict(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = ds(DS_RAW)
    plan = plan_module(module, DS_WITHOUT_ECMP)
    store = store_of(module, conditional_check=conditional_check)
    # The module changes after the version read, so the update itself is stale.
    client = await mock_vco_client(
        changing_after_the_version_read(store, store, 1, other_key)
    )
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


@pytest.mark.parametrize("return_data", [True, False])
async def test_a_module_with_no_level_comes_back_with_none(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = ds(DS_RAW)
    assert module.config_context is None
    plan = plan_module(module, {**DS_RAW, "otherKey": 1})
    store = store_of(module, return_data=return_data)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
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


def without_an_action(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
    """Store an inbound rule the models do not take: it has no ``action``."""
    new["data"]["inbound"] = [{"name": "a", "match": {}}]


def returning(
    store: ModuleStore, change: Callable[[dict[str, Any]], None]
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that sends each request to *store* and lets *change* edit the row a write returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        response = store(request)
        method = parse_json_object(request.content)["method"]
        if method == "configuration/getConfiguration":
            return response
        body: dict[str, Any] = json.loads(response.content)
        change(body["result"])
        return httpx.Response(200, json=body)

    return handler


async def test_counted_update_that_cannot_be_read_back_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    module.data.syslog_forwarding.set_local(True)
    plan = plan_module(module)
    store = store_of(module, return_data=False)
    client = await mock_vco_client(reading(store, []))
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan)
    error = exc.value
    assert not isinstance(error, ConfigConflictError | WriteNotAppliedError)
    assert error.answer == "counted"
    assert error.detail == "its configuration does not hold the module"
    assert error.module is None
    assert error.plan is plan
    assert (error.module_id, error.module_name, error.version) == (1, "firewall", "v1")
    assert (error.lost_edits, error.applied, error.left_out) == ((), (), ())
    assert error.server_changes == ()
    assert str(error).splitlines() == [
        (
            "Configuration module 1 (firewall): the module as stored could not be "
            "read: its configuration does not hold the module"
        ),
        "The VCO counted the write, so the changes below are most likely stored.",
        "planned, not checked:",
        "  firewall_enabled: read true, meant false",
        "  syslog_forwarding: read false, meant true",
        "Read the configuration again and compare before you write this module again.",
    ]
    assert store.rows[1]["data"]["firewall_enabled"] is False
    # Nothing is lost: the caller's module still has the edits.
    assert (error.redo_patch(), error.not_redone()) == ({}, ())


async def test_refused_update_that_cannot_be_read_back_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, conditional_check=True)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(reading(store, []))
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    error = exc.value
    assert error.answer == "refused"
    assert error.detail == "its configuration does not hold the module"
    assert error.module is None
    assert error.lost_edits == ()
    assert isinstance(error.__context__, ConfigConflictError)
    assert str(error).splitlines()[1] == (
        "The VCO refused this write, so it stored nothing of it. Whether the "
        "changes below are stored already is not known."
    )


async def test_an_unknown_outcome_names_the_refs_it_sent(
    mock_vco_client: MockVcoClientFactory,
):
    module = primary_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, None)
    plan = plan_module(module, current)
    store = store_of(module, conditional_check=True)
    # The module changes after the version read, so the update itself is refused.
    client = await mock_vco_client(
        changing_after_the_version_read(
            reading(store, [], CONFIG), store, module.id_, elsewhere
        )
    )
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.plan is plan
    assert str(exc.value).splitlines()[2:4] == [
        "planned, not checked:",
        f"  - refs {PRIMARY} (Global Segment): {DNS_A}",
    ]


async def test_an_unknown_outcome_shows_a_place_and_an_order(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw({**RAW, "inbound": [inbound_rule("a"), inbound_rule("b")]}, layer=Edge)
    rule_type = type(module.data.inbound[0])
    module.data.inbound.reverse()
    module.data.inbound.insert(0, rule_type.model_validate(inbound_rule("c")))
    store = store_of(module, conditional_check=True)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(reading(store, []))
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    lines = str(exc.value).splitlines()
    assert lines[2] == "planned, not checked:"
    assert lines[3].startswith(
        '  inbound[c] (first): read (nothing), meant {"action": {"type": '
    )
    assert lines[4] == (
        '  inbound: order: b after c: read ["a", "b"], meant ["c", "b", "a"]'
    )


async def test_counted_insert_that_cannot_be_read_back_carries_the_id_answered(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module, return_data=False)
    # The read answers with another writer's module of the same name only.
    another = make_module_json(
        "QOS", {"segments": []}, module_id=99, configuration_id=10
    )
    client = await mock_vco_client(reading(store, [another]))
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    (answered,) = store.rows
    assert exc.value.answer == "counted"
    assert exc.value.module_id == answered != 99
    assert exc.value.module is None
    assert exc.value.version == ""


@pytest.mark.parametrize(
    ("conditional_check", "answer"), [(True, "refused"), (False, "counted")]
)
async def test_a_module_with_no_configuration_id_cannot_be_read_back(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool, answer: str
):
    module = firewall_disabled()
    plan = plan_module(module)
    store = store_of(module, conditional_check=conditional_check, return_data=False)
    if conditional_check:
        store.other_writer(1, elsewhere)
    module.configuration_id = None
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan)
    assert exc.value.answer == answer
    assert exc.value.detail == "the module has no configuration ID to read it by"
    assert methods(store) == ["updateConfigurationModule"]


async def test_update_answer_that_does_not_parse_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    store.on_store.append(without_an_action)
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    error = exc.value
    assert error.answer == "row"
    assert error.detail == "the VCO's answer to the update does not parse"
    assert isinstance(error.__cause__, ValidationError)
    assert str(error).splitlines()[1] == (
        "Whether the changes below are stored is not known."
    )
    assert store.rows[1]["data"]["firewall_enabled"] is False


async def test_insert_answer_that_does_not_parse_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    def segments_as_text(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
        new["data"]["segments"] = "not a list"

    module = new_qos_with_a_segment()
    store = store_of(module)
    store.on_store.append(segments_as_text)
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.answer == "row"
    assert exc.value.detail == "the VCO's answer to the insert does not parse"
    assert isinstance(exc.value.__cause__, ValidationError)
    assert len(store.rows) == 1


@pytest.mark.parametrize(
    ("conditional_check", "answer"), [(True, "refused"), (False, "counted")]
)
async def test_read_back_row_that_does_not_parse_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool, answer: str
):
    module = firewall_disabled()
    store = store_of(module, conditional_check=conditional_check, return_data=False)
    if conditional_check:
        store.other_writer(1, lambda row: without_an_action(row, None))
    else:
        store.on_store.append(without_an_action)
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.answer == answer
    assert exc.value.detail == "the module row read back does not parse"
    assert isinstance(exc.value.__cause__, ValidationError)


async def test_read_back_answer_that_does_not_parse_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module, return_data=False)

    def modules_as_text(request: httpx.Request) -> httpx.Response:
        if (
            parse_json_object(request.content)["method"]
            != "configuration/getConfiguration"
        ):
            return store(request)
        config = {**store.configurations[10], "modules": "not a list"}
        return httpx.Response(200, json=make_jsonrpc_response(result=config))

    client = await mock_vco_client(modules_as_text)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.answer == "counted"
    assert exc.value.detail == "the configuration read back does not parse"
    assert exc.value.module_id == 1
    assert isinstance(exc.value.__cause__, ValidationError)
    assert store.rows[1]["data"]["firewall_enabled"] is False


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"id": 999}, "the VCO answered with module 999, not module 1"),
        (
            {"configurationId": 77},
            "the VCO answered with a module of configuration 77, not 10",
        ),
    ],
)
async def test_a_returned_row_of_another_module_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory, change: dict[str, Any], detail: str
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(returning(store, lambda row: row.update(change)))
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.answer == "row"
    assert exc.value.detail == detail
    assert exc.value.module_id == 1


async def test_a_module_held_as_another_class_is_an_unknown_outcome(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    store.rows[1]["name"] = "QOS"
    client = await mock_vco_client(store)
    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.answer == "row"
    assert exc.value.detail == (
        "the VCO holds module 1 as a QosConfigurationModule[Edge], "
        "not a FirewallConfigurationModule[Edge]"
    )


def no_version(row: dict[str, Any]) -> None:
    row["version"] = None


async def test_a_row_returned_without_its_version_is_read_back(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(returning(store, no_version))
    write = await client.write_module(module, plan_module(module))
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    assert write.module.version == store.rows[1]["version"] != "v1"
    assert paths(write.applied) == ["firewall_enabled"]


async def test_an_inserted_row_returned_without_its_version_is_read_back(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module)
    client = await mock_vco_client(returning(store, no_version))
    write = await client.write_module(module, plan_module(module))
    assert methods(store) == ["insertConfigurationModule", "getConfiguration"]
    (stored_id,) = store.rows
    assert (write.module.id_, write.module.version) == (
        stored_id,
        store.rows[stored_id]["version"],
    )


async def test_a_stale_row_returned_without_its_version_is_still_a_conflict(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    store.other_writer(1, elsewhere)
    client = await mock_vco_client(returning(store, no_version))
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.module is not None
    assert exc.value.module.version == store.rows[1]["version"]
    assert paths(exc.value.lost_edits) == ["firewall_enabled"]


REFUSAL: dict[str, Any] = {
    "code": -32003,
    "message": "validationError",
    "data": {
        "valid": False,
        "error": [
            {
                "code": PRECONDITION_FAILED,
                "message": "module 1 has changed",
                "path": "configuration.updateConfigurationModule",
            }
        ],
        "warning": [],
    },
}


def refusing_with_status(
    store: ModuleStore, status: int
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that answers an update with the precondition refusal at HTTP *status*, and sends the rest to *store*."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_json_object(request.content)
        if body["method"] != "configuration/updateConfigurationModule":
            return store(request)
        store.requests.append((str(body["method"]), {}))
        return httpx.Response(status, json=make_jsonrpc_response(error=REFUSAL))

    return handler


@pytest.mark.parametrize("status", [400, 409, 412])
async def test_precondition_refusal_in_an_http_error_is_read_back_and_checked(
    mock_vco_client: MockVcoClientFactory, status: int
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(refusing_with_status(store, status))
    with pytest.raises(ConfigConflictError) as exc:
        _ = await client.write_module(module, plan_module(module))
    assert exc.value.stated is True
    assert paths(exc.value.lost_edits) == ["firewall_enabled"]
    assert isinstance(exc.value.module, FirewallConfigurationModule)
    assert methods(store) == ["updateConfigurationModule", "getConfiguration"]
    refusal = exc.value.__cause__
    assert isinstance(refusal, ConfigConflictError)
    assert isinstance(refusal.__cause__, VcoHttpError)
    assert refusal.__cause__.status_code == status


@pytest.mark.parametrize("conditional_check", [True, False])
async def test_every_request_of_an_update_names_the_enterprise(
    mock_vco_client: MockVcoClientFactory, conditional_check: bool
):
    module = firewall_disabled()
    store = store_of(
        module, conditional_check=conditional_check, return_data=False, enterprise_id=7
    )
    if conditional_check:
        store.other_writer(1, elsewhere)
    client = await mock_vco_client(store)
    if conditional_check:
        with pytest.raises(ConfigConflictError) as exc:
            _ = await client.write_module(module, plan_module(module), enterprise_id=7)
        assert isinstance(exc.value.module, FirewallConfigurationModule)
    else:
        write = await client.write_module(module, plan_module(module), enterprise_id=7)
        assert write.module.raw_data == store.rows[1]["data"]
    assert [
        (method, params.get("enterpriseId")) for method, params in store.requests
    ] == [
        ("configuration/updateConfigurationModule", 7),
        ("configuration/getConfiguration", 7),
    ]


@pytest.mark.parametrize("return_data", [True, False])
async def test_every_request_of_an_insert_names_the_enterprise(
    mock_vco_client: MockVcoClientFactory, return_data: bool
):
    module = new_qos_with_a_segment()
    store = store_of(module, return_data=return_data, enterprise_id=7)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module), enterprise_id=7)
    assert write.module.is_new is False
    assert [params.get("enterpriseId") for _, params in store.requests] == [7, 7]


async def test_if_absent_inserts_when_the_configuration_has_no_module_of_the_name(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module), if_absent=True)
    assert methods(store) == [
        "getConfiguration",
        "insertConfigurationModule",
        "getConfiguration",
    ]
    assert write.operation == "insert"
    (stored_id,) = store.rows
    assert write.module.id_ == stored_id


async def test_if_absent_takes_a_module_of_the_name_as_the_answer(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment().for_edge()
    plan = plan_module(module)
    store = store_of(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)  # the insert whose answer is lost
    (stored_id,) = store.rows

    write = await client.write_module(module, plan, if_absent=True)

    assert methods(store) == [
        "insertConfigurationModule",
        "getConfiguration",
        "getConfiguration",
    ]
    assert list(store.rows) == [stored_id]
    assert (write.operation, write.module.id_) == ("insert", stored_id)
    assert write.module.is_new is False
    assert write.module.config_context is module.config_context is ConfigContext.EDGE
    assert paths(write.applied) == ['segments["Global Segment"]']


async def test_if_absent_checks_the_module_it_finds_against_the_plan(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module)
    another = make_module_json(
        "QOS", {"segments": []}, module_id=99, configuration_id=10
    )
    store.rows[99] = another
    client = await mock_vco_client(store)

    with pytest.raises(WriteNotAppliedError) as exc:
        _ = await client.write_module(module, plan_module(module), if_absent=True)

    assert methods(store) == ["getConfiguration"]
    assert exc.value.module_id == 99
    assert isinstance(exc.value.module, QosConfigurationModule)
    assert paths(exc.value.lost_edits) == ['segments["Global Segment"]']


async def test_if_absent_takes_the_newest_of_several(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    plan = plan_module(module)
    store = store_of(module)
    store.rows[7] = make_module_json(
        "QOS", {"segments": []}, module_id=7, configuration_id=10
    )
    store.rows[8] = make_module_json(
        "QOS", copy.deepcopy(plan.data), module_id=8, configuration_id=10
    )
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan, if_absent=True)
    assert write.module.id_ == 8


async def test_if_absent_changes_nothing_for_an_update(
    mock_vco_client: MockVcoClientFactory,
):
    module = firewall_disabled()
    store = store_of(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan_module(module), if_absent=True)
    assert methods(store) == ["updateConfigurationModule"]


async def test_if_absent_cannot_take_a_row_that_does_not_parse(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    plan = plan_module(module)
    store = store_of(module)
    store.rows[99] = make_module_json(
        "QOS", {"segments": "not a list"}, module_id=99, configuration_id=10
    )
    client = await mock_vco_client(store)

    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan, if_absent=True)

    assert methods(store) == ["getConfiguration"]  # nothing is inserted beside it
    assert (exc.value.answer, exc.value.module_id) == ("row", 99)
    assert exc.value.detail == (
        "the configuration holds a module of this name whose row does not parse"
    )
    assert exc.value.plan is plan
    assert isinstance(exc.value.__cause__, ValidationError)


async def test_if_absent_reads_only_the_rows_of_the_name(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    store = store_of(module)
    # Another module of the configuration, which the models do not take.
    store.rows[2] = make_module_json(
        "firewall", {"segments": "not a list"}, module_id=2, configuration_id=10
    )
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan_module(module), if_absent=True)
    assert methods(store) == [
        "getConfiguration",
        "insertConfigurationModule",
        "getConfiguration",
    ]
    assert write.module.name == "QOS"


async def test_if_absent_cannot_use_a_configuration_that_does_not_parse(
    mock_vco_client: MockVcoClientFactory,
):
    module = new_qos_with_a_segment()
    plan = plan_module(module)
    store = store_of(module)

    def modules_as_text(request: httpx.Request) -> httpx.Response:
        if (
            parse_json_object(request.content)["method"]
            != "configuration/getConfiguration"
        ):
            return store(request)
        config = {**store.configurations[10], "modules": "not a list"}
        return httpx.Response(200, json=make_jsonrpc_response(result=config))

    client = await mock_vco_client(modules_as_text)

    with pytest.raises(WriteOutcomeUnknownError) as exc:
        _ = await client.write_module(module, plan, if_absent=True)

    assert methods(store) == []  # no insert was sent
    assert store.rows == {}
    assert exc.value.answer == "row"
    assert exc.value.detail == (
        "the configuration read before sending the insert again does not parse, "
        "and an earlier insert of this module may be stored"
    )
    assert isinstance(exc.value.__cause__, ValidationError)
    lines = str(exc.value).splitlines()
    assert lines[0] == (
        "Configuration module 0 (QOS): the module as stored could not be read: "
        f"{exc.value.detail}"
    )
    assert lines[1:3] == [
        "Whether the changes below are stored is not known.",
        "planned, not checked:",
    ]
    assert "sent" not in " ".join(lines[:3])


def never_read() -> FirewallConfigurationModule[Edge]:
    """Firewall module 1 of configuration 10, built without a read: it has no raw data."""
    module = FirewallConfigurationModule[Edge].model_construct(
        name="firewall",
        data=FirewallData[Edge](segments=[], firewall_enabled=False),
        id_=1,
        version="v1",
        configuration_id=10,
    )
    assert module.raw_data is MISSING
    return module


async def test_module_that_never_carried_data_is_written_whole(
    mock_vco_client: MockVcoClientFactory,
):
    module = never_read()
    store = store_of(fw(RAW, layer=Edge))
    client = await mock_vco_client(store)
    plan = plan_module(module)
    write = await client.write_module(module, plan)
    ((_, params),) = store.requests
    assert params["_update"]["data"] == module.data.model_dump(
        mode="json", by_alias=True
    )
    assert (params["version"], params["returnData"]) == ("v1", True)
    # Nothing was read, so nothing the VCO held is kept: ``future`` is gone.
    assert store.rows[1]["data"] == plan.data
    assert "future" not in store.rows[1]["data"]
    assert paths(write.applied) == sorted(plan.data)
    assert (write.server_changes, write.left_out) == ((), ())
    assert write.module.raw_data == plan.data
    assert plan_module(write.module).operation == "none"
