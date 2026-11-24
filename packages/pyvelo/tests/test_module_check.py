"""The outcome of one module write: what the VCO stored, against what the plan meant to change."""

import copy
import dataclasses
import json
import uuid
from collections.abc import Callable, Iterable
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client import VcoClient
from pyvelo.client.derived_refs import (
    APPEND_ONLY_REF_NAMES,
    PROCESSOR_REF_NAMES,
    RefChange,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    RefsDroppedError,
    WriteNotAppliedError,
)
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.rules import FirewallInboundRule
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.models.configuration.wan.link import WanLink
from pyvelo.client.module_check import (
    MODULE_REWRITES,
    ModuleWrite,
    check_module_write,
    check_refs,
    has_checkable_change,
)
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    new_edge_module,
)
from pyvelo.core.identity import NetworkServiceIdentity, SegmentIdentity
from pyvelo.core.layers import Edge
from pyvelo.core.rebase import Key, Path
from pyvelo.core.response_check import (
    PathPattern,
    PlannedChange,
    ResponseCheck,
    ServerChange,
    check_response,
)
from pyvelo.testing import ModuleStore, as_stored, make_module_json, store_of
from refs_helpers import (
    BACKUP,
    CONFIG,
    DNS_A,
    DNS_B,
    DS_ID,
    PRIMARY,
    SEG0,
    SEG0_OBJECT,
    SEGMENT,
    changing_after_the_version_read,
    copy_of_data,
    dns,
    ds_module,
    primary_provider_module,
    primary_provider_row,
    segment_entry,
    segment_row,
    service_row,
    set_primary_provider,
    store_with_services,
    table_registry,
    unset_provider_module,
)

from ._type_support import MockVcoClientFactory
from .write_helpers import fw


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    """The path of each entry, sorted: the order within one tuple is not asserted."""
    return sorted(str(x.path) for x in xs)


def qos(raw: dict[str, Any], *, refs: object = MISSING) -> QosConfigurationModule:
    return QosConfigurationModule.model_validate(
        make_module_json(
            "QOS", copy.deepcopy(raw), configuration_id=10, refs=copy.deepcopy(refs)
        )
    )


def ds(raw: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(raw), configuration_id=10)
    )


def wan(raw: dict[str, Any]) -> WanConfigurationModule:
    return WanConfigurationModule.model_validate(
        make_module_json("WAN", copy.deepcopy(raw), configuration_id=10)
    )


def inbound(name: str, **fields: object) -> FirewallInboundRule:
    return FirewallInboundRule.model_validate(
        {"name": name, "match": {}, "action": {"type": "port_forwarding"}, **fields}
    )


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


RAW: dict[str, Any] = {
    "segments": [],
    "inbound": [],
    "firewall_enabled": True,
    "syslog_forwarding": False,
    "future": 1,
}
"""A firewall module's data; ``future`` is a key the models do not know."""

QOS_RAW: dict[str, Any] = {"segments": []}
DS_RAW: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [{"segment": desc(0, "Global Segment")}],
}

ROW1: dict[str, Any] = {
    "id": 101,
    "enterpriseObjectId": 5,
    "segmentObjectId": 9,
    "logicalId": "a5",
}
ROW2: dict[str, Any] = {"id": 102, "enterpriseObjectId": 6, "logicalId": "a6"}
ADDRESS_GROUP = "objectGroup:addressGroup"
U1, U2, U3 = (str(uuid.UUID(int=n)) for n in (1, 2, 3))
"""WAN link ``internalId`` values."""


def firewall_disabled() -> FirewallConfigurationModule[Edge]:
    """The firewall module with one planned change: ``firewall_enabled`` off."""
    module = fw(RAW, layer=Edge)
    module.data.firewall_enabled = False
    return module


def test_every_change_applied_returns_the_stored_module():
    module = firewall_disabled()
    plan = plan_module(module)
    stored = as_stored(module, plan.data)
    write = check_module_write(module, plan, stored)
    assert write.module is stored
    assert write.plan is plan
    assert write.operation == "update"
    assert paths(write.applied) == ["firewall_enabled"]


def test_nothing_applied_and_as_read_is_a_conflict():
    module = firewall_disabled()
    plan = plan_module(module)
    stored = as_stored(module, RAW)
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored)
    error = exc.value
    assert error.module is stored
    assert error.plan is plan
    assert error.stated is False
    assert paths(error.lost_edits) == ["firewall_enabled"]
    assert error.applied == ()
    assert error.detail == (
        "none of the planned changes is stored, and the values there are as read"
    )
    assert str(error).splitlines()[0] == (
        "Configuration module 1 (firewall): none of the planned changes is stored, "
        "and the values there are as read. The VCO seems to have refused the write "
        "(version v1) without saying so, as it does when the module changed since "
        "it was read"
    )


def test_nothing_applied_but_moved_is_not_applied():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "future": 2})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {**RAW, "future": 3}))
    assert exc.value.lost_edits[0].clash
    assert "None is stored" in str(exc.value)


def test_partial_lists_applied_and_lost_apart():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "firewall_enabled": False, "future": 2})
    stored = as_stored(module, {**RAW, "firewall_enabled": False})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    error = exc.value
    assert paths(error.lost_edits) == ["future"]
    assert paths(error.applied) == ["firewall_enabled"]
    text = str(error)
    assert "1 of 2 planned changes" in text
    assert "None is stored" not in text
    assert "  future: read 1, meant 2, stored 1" in text.splitlines()
    assert "  firewall_enabled" in text.splitlines()


def test_refused_with_a_change_missing_is_a_conflict():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "future": 2})
    stored = as_stored(module, {**RAW, "future": 3})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    assert exc.value.detail == "the VCO refused the version"
    assert exc.value.lost_edits[0].clash


def test_refused_with_every_change_stored_passes():
    module = firewall_disabled()
    plan = plan_module(module)
    write = check_module_write(
        module, plan, as_stored(module, plan.data), answer="refused"
    )
    assert isinstance(write, ModuleWrite)
    assert paths(write.applied) == ["firewall_enabled"]


def test_insert_not_stored_is_not_applied():
    module = new_edge_module(ConfigurationModuleName.FIREWALL, 10)
    assert isinstance(module, FirewallConfigurationModule)
    module.data.inbound.append(inbound("a"))
    plan = plan_module(module)
    assert plan.operation == "insert"
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {"segments": []}))
    assert not isinstance(exc.value, ConfigConflictError)
    assert exc.value.version == ""
    assert "None is stored" not in str(exc.value)  # nothing there moved


def test_conflict_lists_what_another_writer_changed():
    module = firewall_disabled()
    plan = plan_module(module)
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {**RAW, "future": 2}))
    assert paths(exc.value.server_changes) == ["future"]
    (change,) = exc.value.server_changes
    assert (change.as_read, change.stored) == (1, 2)
    lines = str(exc.value).splitlines()
    assert lines[-2:] == ["stored otherwise than sent:", "  future: read 1, stored 2"]


def test_message_names_each_path_and_marks_a_clash():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "future": 2, "added": "x"})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {**RAW, "future": 3}))
    lines = str(exc.value).splitlines()
    assert (
        "lost edits (not stored; `error.redo_patch()` redoes those it can, `error.not_redone()` lists the rest):"
        in lines
    )
    assert (
        "  future: read 1, meant 2, stored 3  [clash: also changed on the VCO]" in lines
    )
    assert '  added: read (nothing), meant "x", stored (nothing)' in lines
    assert str(exc.value).count("[clash: also changed on the VCO]") == 1


def test_long_values_are_cut():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "future": "x" * 500})
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan, as_stored(module, RAW))
    text = str(exc.value)
    assert all(len(line) <= 400 for line in text.splitlines())
    assert "…" in text


def test_long_values_are_cut_where_they_differ():
    old, new = list(range(60)), [*range(55), 99, *range(56, 60)]
    module = fw({**RAW, "future": old}, layer=Edge)
    plan = plan_module(module, {**RAW, "future": new})
    stored = as_stored(module, {**RAW, "future": old, "syslog_forwarding": True})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored)
    assert str(exc.value).splitlines()[1:] == [
        "lost edits (not stored; `error.redo_patch()` redoes those it can, `error.not_redone()` lists the rest):",
        (
            "  future: read …50, 51, 52, 53, 54, 55, 56, 57, 58, 59], "
            "meant …50, 51, 52, 53, 54, 99, 56, 57, 58, 59], "
            "stored …50, 51, 52, 53, 54, 55, 56, 57, 58, 59]"
        ),
        "stored otherwise than sent:",
        "  syslog_forwarding: read false, stored true",
    ]


def inbound_rows(*names: str) -> list[dict[str, Any]]:
    """Inbound rules as the VCO returns them, each with a ``ruleLogicalId``."""
    return [
        inbound(name, ruleLogicalId=f"l-{name}").model_dump(mode="json")
        for name in names
    ]


def test_message_shows_the_place_of_an_added_rule_and_the_order():
    rows = inbound_rows("a", "b")
    module = fw({**RAW, "inbound": rows}, layer=Edge)
    module.data.inbound.insert(0, inbound("c"))
    module.data.inbound.append(module.data.inbound.pop(1))  # c, b, a
    plan = plan_module(module)
    stored = as_stored(module, {**RAW, "inbound": rows, "future": 2})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    lines = str(exc.value).splitlines()
    assert (
        lines[1]
        == "lost edits (not stored; `error.redo_patch()` redoes those it can, `error.not_redone()` lists the rest):"
    )
    assert lines[2].startswith(
        '  inbound[c] (first): read (nothing), meant {"action": '
    )
    assert lines[2].endswith("…, stored (nothing)")
    assert lines[3] == (
        '  inbound: order: b after c: read ["a", "b"], meant ["c", "b", "a"], '
        'stored ["a", "b"]'
    )


def test_message_names_a_stored_order():
    module = fw({**RAW, "inbound": inbound_rows("a", "b")}, layer=Edge)
    module.data.inbound.reverse()
    module.data.firewall_enabled = False
    plan = plan_module(module)
    stored = as_stored(module, {**plan.data, "firewall_enabled": True})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    assert str(exc.value).splitlines()[-2:] == ["already stored:", "  inbound: order"]


def test_redo_patch_rebuilds_a_place_and_a_move():
    rows = inbound_rows("a", "b")
    module = fw({**RAW, "inbound": rows}, layer=Edge)
    module.data.inbound.insert(0, inbound("c"))
    module.data.inbound.append(module.data.inbound.pop(1))  # c, b, a
    module.data.inbound[1].logging_enabled = True
    plan = plan_module(module)
    stored = as_stored(module, {**RAW, "inbound": rows, "future": 2})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    error = exc.value

    patch = error.redo_patch()
    assert list(patch) == ["inbound"]
    ops = cast(dict[str, dict[str, object]], patch["inbound"])
    assert list(ops) == ["b", "c"]  # b goes after c, so it is written before it
    assert ops["b"] == {"logging_enabled": True, "_position": "first"}
    assert (ops["c"]["_op"], ops["c"]["name"], ops["c"]["_position"]) == (
        "add",
        "c",
        "first",
    )
    assert error.not_redone() == ()

    assert isinstance(error.module, FirewallConfigurationModule)
    _ = error.module.data.merge_patch(patch)
    again = plan_module(error.module)
    assert again.changes == plan.changes
    assert again.render() == plan.render()


def test_redo_patch_leaves_out_a_clash_and_redoes_a_provider_row():
    module = primary_provider_module()
    current = copy_of_data(module)
    _ = current.merge_patch({"ntp": {"enabled": True}})
    set_primary_provider(current, None)
    plan = plan_module(module, current)
    theirs = {"enabled": False, "servers": []}
    stored = as_stored(module, {**DS_ROW_RAW, "ntp": theirs})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    error = exc.value
    assert paths(error.lost_edits) == sorted([PRIMARY_ROW_PATH, "ntp"])
    assert error.redo_patch() == a_service_set(None)
    assert paths(error.not_redone()) == ["ntp"]
    assert error.redo_patch(include_clashes=True) == {
        "ntp": plan.data["ntp"],
        **a_service_set(None),
    }
    assert error.not_redone(include_clashes=True) == ()


def test_redo_patch_never_sets_another_writers_unkeyed_item():
    # A link added without an internalId has no key, so the check pairs it
    # with the link another writer put at the front.
    def link(name: str, internal_id: str) -> dict[str, Any]:
        return {
            "internalId": internal_id,
            "name": name,
            "type": "WIRED",
            "addressingVersion": "IPv4",
        }

    ours = link("ours", U1)
    del ours["internalId"]
    module = wan({"links": [link("a", U2)]})
    assert isinstance(module.data.links, list)
    module.data.links.insert(0, WanLink.model_validate(ours))
    plan = plan_module(module)
    stored = as_stored(module, {"links": [link("theirs", U3), link("a", U2)]})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    error = exc.value
    assert error.lost_edits[0].as_read is MISSING
    assert error.redo_patch() == {}

    patch = error.redo_patch(include_clashes=True)
    links = cast(dict[str, dict[str, object]], patch["links"])
    assert (links["+"]["_op"], links["+"]["_position"]) == ("add", "first")
    assert isinstance(error.module, WanConfigurationModule)
    _ = error.module.data.merge_patch(patch)  # their link is not overwritten
    assert isinstance(error.module.data.links, list)
    assert [link.name for link in error.module.data.links] == ["ours", "theirs", "a"]


def test_completed_writes_are_named():
    module = firewall_disabled()
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan_module(module), as_stored(module, RAW))
    assert "written before this one" not in str(exc.value)

    earlier = qos(QOS_RAW)
    earlier_plan = plan_module(earlier, {**QOS_RAW, "future": 1})
    exc.value.completed = (
        ModuleWrite(
            name=earlier_plan.name,
            operation="update",
            module=as_stored(earlier, earlier_plan.data),
            plan=earlier_plan,
        ),
    )
    assert "written before this one: QOS (update)" in str(exc.value)


async def test_ref_row_added_must_be_returned():
    module, plan = await provider_set(with_data_change=True)
    reissued = {
        **raw_refs(module),
        PRIMARY: [{**primary_provider_row(), "id": 201}],
    }
    write = check_module_write(
        module, plan, as_stored(module, plan.data, refs=reissued)
    )
    assert paths(c for c in write.applied if c.part == "refs") == [PRIMARY_ROW_PATH]

    with pytest.raises(RefsDroppedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, plan.data))
    assert exc.value.lost_edits[0].part == "refs"
    assert paths(exc.value.lost_edits) == [PRIMARY_ROW_PATH]
    assert "refs " in str(exc.value)


def test_ref_row_removed_must_be_gone():
    module, plan = provider_cleared()
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, DS_ROW_RAW))
    assert paths(exc.value.lost_edits) == [PRIMARY_ROW_PATH]
    assert exc.value.applied == ()


async def test_refs_only_write_refused_is_a_conflict():
    module, plan = await provider_set()
    assert plan.data == DS_ROW_RAW
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, DS_ROW_RAW))
    (lost,) = exc.value.lost_edits
    assert (lost.path, lost.part) == (PRIMARY_ROW_PATH, "refs")
    assert (lost.as_read, lost.intended, lost.stored) == (
        MISSING,
        plan.ref_changes[0].row,
        MISSING,
    )


async def test_stale_data_with_refs_replaced_is_not_applied_and_not_a_conflict():
    """A VCO with the check off that kept the data as it was and replaced the refs."""
    module, plan = await provider_set(with_data_change=True)
    replaced = {
        **raw_refs(module),
        PRIMARY: [{**primary_provider_row(), "id": 201}],
    }
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(
            module, plan, as_stored(module, DS_ROW_RAW, refs=replaced)
        )
    assert [(c.part, str(c.path)) for c in exc.value.applied] == [
        ("refs", PRIMARY_ROW_PATH)
    ]
    assert [(c.part, str(c.path)) for c in exc.value.lost_edits] == [("data", "ntp")]


def test_refs_the_vco_dropped_on_its_own():
    module = qos(QOS_RAW, refs={"a": [ROW1, ROW2]})
    plan = plan_module(module, {**QOS_RAW, "future": 1})
    assert plan.refs is None
    write = check_module_write(
        module, plan, as_stored(module, plan.data, refs={"a": [ROW1]})
    )
    (change,) = [c for c in write.server_changes if c.part == "refs"]
    assert change.path == "a[a6]"
    assert change.as_read == ROW2
    assert change.stored is MISSING


def test_firewall_rule_id_revised_passes():
    module = fw(RAW, layer=Edge)
    module.data.inbound.append(inbound("a", ruleLogicalId="x"))
    plan = plan_module(module)
    stored_data = copy.deepcopy(plan.data)
    stored_data["inbound"][0]["ruleLogicalId"] = "y"
    write = check_module_write(module, plan, as_stored(module, stored_data))
    assert paths(write.applied) == ["inbound[a]"]
    (change,) = write.server_changes
    assert change.path == "inbound[a].ruleLogicalId"
    assert change.stored == "y"


def test_a_write_whose_only_changes_are_left_out_passes():
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]}, layer=Edge)
    module.data.inbound[0].rule_logical_id = "x2"
    plan = plan_module(module)
    stored_data = copy.deepcopy(plan.data)
    stored_data["inbound"][0]["ruleLogicalId"] = "y"
    write = check_module_write(module, plan, as_stored(module, stored_data))
    assert isinstance(write, ModuleWrite)
    assert paths(write.left_out) == ["inbound[a].ruleLogicalId"]
    assert write.applied == ()


def test_write_error_lists_what_is_left_out():
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]}, layer=Edge)
    plan = plan_module(
        module,
        {
            **RAW,
            "inbound": [{**rule, "ruleLogicalId": "x2"}],
            "firewall_enabled": False,
            "future": 2,
        },
    )
    # The VCO keeps the rule's ID as read, stores one change and not the other.
    stored = as_stored(module, {**RAW, "inbound": [rule], "firewall_enabled": False})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    assert paths(exc.value.lost_edits) == ["future"]
    assert paths(exc.value.applied) == ["firewall_enabled"]
    assert paths(exc.value.left_out) == ["inbound[a].ruleLogicalId"]
    (change,) = exc.value.left_out
    assert (change.as_read, change.intended, change.stored) == ("x", "x2", "x")


def test_refs_not_read_and_none_sent_are_no_server_changes():
    module = qos(QOS_RAW)
    assert module.raw_refs is MISSING
    plan = plan_module(module, {**QOS_RAW, "future": 1})
    assert plan.refs is None
    stored = as_stored(module, plan.data, refs={"a": [ROW1]})
    write = check_module_write(module, plan, stored)
    assert write.server_changes == ()
    assert check_refs(module, plan, stored) == ResponseCheck()


def test_wan_link_internal_id_issued_passes():
    module = wan({"links": []})
    link = {"name": "l1", "type": "WIRED", "addressingVersion": "IPv4"}
    plan = plan_module(module, {"links": [link]})
    issued = {**link, "internalId": "00000000-0000-0000-0000-000000000001"}
    write = check_module_write(module, plan, as_stored(module, {"links": [issued]}))
    assert paths(write.applied) == ["links[#0]"]  # sent without a key
    (change,) = write.server_changes
    assert change.path == "links[#0].internalId"
    assert change.stored == issued["internalId"]


def test_wan_internal_id_changed_and_stored_otherwise_passes():
    # A link read without an internalId has no key, so the links are compared
    # as a list replaced whole, and the first link's ID, a ServerOwned field,
    # may be stored as another value than the one sent. (A keyed link whose
    # internalId changes is another item: the ID is part of its key.)
    first = {
        "internalId": U1,
        "name": "l1",
        "type": "WIRED",
        "addressingVersion": "IPv4",
    }
    keyless = {"name": "l2", "type": "WIRED", "addressingVersion": "IPv4"}
    module = wan({"links": [first, keyless]})
    plan = plan_module(module, {"links": [{**first, "internalId": U2}, keyless]})
    stored = {"links": [{**first, "internalId": U3}, keyless]}
    write = check_module_write(module, plan, as_stored(module, stored))
    assert paths(write.applied) == ["links"]
    (change,) = write.server_changes
    assert change.path == "links[#0].internalId"
    assert change.stored == U3


def test_ipv6_in_another_form_passes_in_device_settings():
    module = ds(DS_RAW)
    plan = plan_module(module, {**DS_RAW, "note6": "2001:DB8::1"})
    stored = as_stored(module, {**DS_RAW, "note6": "2001:db8::1"})
    write = check_module_write(module, plan, stored)
    assert paths(write.applied) == ["note6"]


def test_the_same_in_firewall_is_not_applied():
    module = fw(RAW, layer=Edge)
    plan = plan_module(module, {**RAW, "note6": "2001:DB8::1"})
    stored = as_stored(module, {**RAW, "note6": "2001:db8::1"})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, stored)
    assert not isinstance(exc.value, ConfigConflictError)


def test_ecmp_removed_and_added_back_passes():
    hash_params: dict[str, object] = {}
    ecmp = {"maximumPaths": 4, "loadSharing": {"hashParams": hash_params}}
    default = {"maximumPaths": 2, "loadSharing": {"hashParams": hash_params}}
    global_segment = {"segment": desc(0, "Global Segment")}
    module = ds({**DS_RAW, "segments": [{**global_segment, "ecmp": ecmp}]})
    plan = plan_module(module, {**DS_RAW, "segments": [global_segment]})
    stored = as_stored(
        module, {**DS_RAW, "segments": [{**global_segment, "ecmp": default}]}
    )
    write = check_module_write(module, plan, stored)
    assert paths(write.left_out) == ['segments["Global Segment"].ecmp']
    assert write.applied == ()


GLOBAL: dict[str, Any] = {"segment": desc(0, "Global Segment")}
ECMP: dict[str, Any] = {"maximumPaths": 4, "loadSharing": {"hashParams": {}}}
ECMP_DEFAULT: dict[str, Any] = {"maximumPaths": 2, "loadSharing": {"hashParams": {}}}
ECMP_PATH = 'segments["Global Segment"].ecmp'
FIRST: dict[str, Any] = {"id": 301, "enterpriseObjectId": 7}
SECOND: dict[str, Any] = {"id": 302, "enterpriseObjectId": 8}


def ecmp_removed() -> tuple[ConfigurationModuleBase, ModulePlan]:
    """Device settings whose only planned change removes a segment's ``ecmp``, a default the VCO adds back."""
    module = ds({**DS_RAW, "segments": [{**GLOBAL, "ecmp": ECMP}]})
    return module, plan_module(module, {**DS_RAW, "segments": [GLOBAL]})


def owned_id_changed() -> tuple[ConfigurationModuleBase, ModulePlan]:
    """A firewall module whose only planned change is to a rule's ``ruleLogicalId``, which the VCO owns."""
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]}, layer=Edge)
    return module, plan_module(
        module, {**RAW, "inbound": [{**rule, "ruleLogicalId": "x2"}]}
    )


DS_ROW_RAW = cast(dict[str, Any], primary_provider_module().raw_data)
PRIMARY_ROW_PATH = f'"{PRIMARY}"[{DNS_A},{SEG0_OBJECT}]'


def raw_refs(module: ConfigurationModuleBase) -> dict[str, Any]:
    return cast(dict[str, Any], module.raw_refs)


def provider_cleared() -> tuple[DeviceSettingsConfigurationModule, ModulePlan]:
    """Device settings whose only planned change removes the row of a primary provider."""
    module = primary_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, None)
    return module, plan_module(module, current)


async def provider_set(
    *, with_data_change: bool = False
) -> tuple[DeviceSettingsConfigurationModule, ModulePlan]:
    """Device settings that get a primary provider and its row, and beside it ``ntp`` if asked."""
    module = unset_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    if with_data_change:
        _ = current.merge_patch({"ntp": {"enabled": True}})
    resolvers = await table_registry(
        NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
    )
    return module, plan_module(module, current, resolvers=resolvers)


def test_a_change_of_data_is_checkable():
    module = firewall_disabled()
    assert has_checkable_change(module, plan_module(module)) is True


async def test_a_ref_row_added_is_checkable():
    module, plan = await provider_set()
    assert (plan.operation, plan.changes) == ("update", ())
    assert has_checkable_change(module, plan) is True


def test_a_ref_row_removed_is_checkable():
    module, plan = provider_cleared()
    assert has_checkable_change(module, plan) is True


@pytest.mark.parametrize("case", [ecmp_removed, owned_id_changed])
def test_a_plan_of_changes_the_check_cannot_find_stored_is_not_checkable(case: Any):
    module, plan = case()
    assert plan.operation == "update"
    assert has_checkable_change(module, plan) is False


def test_a_removal_of_a_readded_default_beside_another_change_is_checkable():
    module = ds({**DS_RAW, "segments": [{**GLOBAL, "ecmp": ECMP}]})
    plan = plan_module(module, {**DS_RAW, "segments": [GLOBAL], "otherKey": 1})
    assert has_checkable_change(module, plan) is True


def test_refused_and_a_left_out_change_still_as_read_is_a_lost_edit():
    module, plan = ecmp_removed()
    stored = as_stored(module, module.raw_data)
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    error = exc.value
    assert error.stated is True
    assert error.detail == "the VCO refused the version"
    assert paths(error.lost_edits) == [ECMP_PATH]
    assert error.lost_edits[0].clash is False
    assert (error.applied, error.left_out) == ((), ())
    assert error.module is stored


def test_refused_with_a_left_out_change_stored_otherwise_is_a_conflict_too():
    module, plan = ecmp_removed()
    stored = as_stored(
        module, {**DS_RAW, "segments": [{**GLOBAL, "ecmp": ECMP_DEFAULT}]}
    )
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    error = exc.value
    assert error.lost_edits == ()
    assert paths(error.left_out) == [ECMP_PATH]
    lines = str(error).splitlines()
    assert lines[0] == (
        "Configuration module 1 (deviceSettings) changed since it was read "
        "(version v1): the VCO refused the version"
    )
    assert lines[1] == (
        "Nothing is listed as lost: the check cannot tell whether every change of "
        "this write is stored. Compare the module as stored with what you meant, "
        "and make the change again where it is missing."
    )
    assert "lost edits" not in str(error)


def test_refused_with_a_change_stored_and_one_left_out_as_read_is_a_conflict():
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]}, layer=Edge)
    plan = plan_module(
        module,
        {
            **RAW,
            "inbound": [{**rule, "ruleLogicalId": "x2"}],
            "firewall_enabled": False,
        },
    )
    stored = as_stored(module, {**RAW, "inbound": [rule], "firewall_enabled": False})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    assert paths(exc.value.applied) == ["firewall_enabled"]
    assert paths(exc.value.lost_edits) == ["inbound[a].ruleLogicalId"]
    assert exc.value.left_out == ()


def test_a_row_with_nothing_stored_makes_a_left_out_change_as_read_a_lost_edit():
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]}, layer=Edge)
    plan = plan_module(
        module, {**RAW, "inbound": [{**rule, "ruleLogicalId": "x2"}], "future": 2}
    )
    stored = as_stored(module, {**RAW, "inbound": [rule]})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored)
    error = exc.value
    assert error.stated is False
    assert paths(error.lost_edits) == ["future", "inbound[a].ruleLogicalId"]
    assert error.left_out == ()


def test_a_row_whose_only_change_is_left_out_and_as_read_is_a_conflict():
    module, plan = ecmp_removed()
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, module.raw_data))
    assert exc.value.stated is False
    assert paths(exc.value.lost_edits) == [ECMP_PATH]
    assert exc.value.left_out == ()


def test_a_counted_write_keeps_a_left_out_change_as_read_left_out():
    module, plan = ecmp_removed()
    write = check_module_write(
        module, plan, as_stored(module, module.raw_data), answer="counted"
    )
    assert isinstance(write, ModuleWrite)
    assert paths(write.left_out) == [ECMP_PATH]
    assert write.applied == ()


def test_a_counted_write_with_nothing_stored_is_not_a_conflict():
    module = firewall_disabled()
    plan = plan_module(module)
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, RAW), answer="counted")
    assert not isinstance(exc.value, ConfigConflictError)
    assert paths(exc.value.lost_edits) == ["firewall_enabled"]
    assert str(exc.value).splitlines()[0] == (
        "Configuration module 1 (firewall): 1 of 1 planned changes are not stored as sent"
    )


def test_an_inferred_conflict_does_not_state_that_the_module_changed():
    module = firewall_disabled()
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan_module(module), as_stored(module, RAW))
    assert exc.value.stated is False
    assert str(exc.value).splitlines()[0] == (
        "Configuration module 1 (firewall): none of the planned changes is stored, "
        "and the values there are as read. The VCO seems to have refused the write "
        "(version v1) without saying so, as it does when the module changed since "
        "it was read"
    )


def test_str_of_a_write_error_never_raises():
    class Odd:
        def __repr__(self) -> str:
            return "<odd>"

        def __str__(self) -> str:
            raise RuntimeError("no text")

    values: list[object] = [
        uuid.UUID(int=6),
        {1: "a", "b": 2},
        {"when": {uuid.UUID(int=7)}},
        Odd(),
    ]
    error = ModuleWriteError(
        1,
        "v1",
        lost_edits=[
            PlannedChange(Path(("x", n)), MISSING, value, value)
            for n, value in enumerate(values)
        ],
    )
    lines = str(error).splitlines()
    assert len(lines) == 2 + len(values)
    assert '"00000000-0000-0000-0000-000000000006"' in lines[2]
    assert "<odd>" in lines[5]


READDED: dict[
    PathPattern, tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]
] = {
    ("segments", "*", "ecmp"): (
        {"segments": [{**GLOBAL, "ecmp": {"maximumPaths": 4}}]},
        {"segments": [GLOBAL]},
        {"segments": [{**GLOBAL, "ecmp": {"maximumPaths": 2}}]},
        'segments["Global Segment"].ecmp',
    ),
    ("segments", "*", "ospf", "v6Detail"): (
        {"segments": [{**GLOBAL, "ospf": {"v6Detail": {"enabled": True}}}]},
        {"segments": [{**GLOBAL, "ospf": {}}]},
        {"segments": [{**GLOBAL, "ospf": {"v6Detail": {"enabled": False}}}]},
        'segments["Global Segment"].ospf.v6Detail',
    ),
    ("segments", "*", "bgp", "v6Detail", "ospf"): (
        {"segments": [{**GLOBAL, "bgp": {"v6Detail": {"ospf": {"enabled": True}}}}]},
        {"segments": [{**GLOBAL, "bgp": {"v6Detail": {}}}]},
        {"segments": [{**GLOBAL, "bgp": {"v6Detail": {"ospf": {"enabled": False}}}}]},
        'segments["Global Segment"].bgp.v6Detail.ospf',
    ),
    ("**", "fips"): (
        {"ha": {"fips": True}},
        {"ha": {}},
        {"ha": {"fips": False}},
        "ha.fips",
    ),
}
"""Per ``readded`` pattern of device settings: the data as read, as sent and as stored, and the path left out."""


def test_every_rewrite_in_the_table_has_a_case_here():
    assert set(MODULE_REWRITES) == {"deviceSettings"}
    rewrites = MODULE_REWRITES["deviceSettings"]
    assert set(rewrites.readded) == set(READDED)
    assert rewrites.server_owned == (("**", "clientConnector", "name"),)
    assert rewrites.ipv6 is True


@pytest.mark.parametrize("pattern", list(READDED), ids=".".join)
def test_a_default_device_settings_adds_back_is_left_out(pattern: PathPattern):
    read, sent, stored, path = READDED[pattern]
    check = check_response(
        read,
        sent,
        stored,
        DeviceSettingsData,
        rewrites=MODULE_REWRITES["deviceSettings"],
    )
    assert paths(check.left_out) == [path]
    assert check.not_applied == ()


def test_the_client_connector_name_is_owned_in_device_settings():
    check = check_response(
        {"clientConnector": {"name": "a"}},
        {"clientConnector": {"name": "b"}},
        {"clientConnector": {"name": "c"}},
        rewrites=MODULE_REWRITES["deviceSettings"],
    )
    assert paths(check.left_out) == ["clientConnector.name"]
    assert check.not_applied == ()


def test_the_append_only_ref_names():
    assert APPEND_ONLY_REF_NAMES == {
        "deviceSettings:vnfs:edge",
        "objectGroup:addressGroup",
        "objectGroup:portGroup",
    }


def refs_plan(
    module: ConfigurationModuleBase, refs: dict[str, Any], *changes: RefChange
) -> ModulePlan:
    """A plan of *module* that sends *refs* and plans *changes* of them; its data is not changed."""
    plan = plan_module(module, {**QOS_RAW, "future": 1})
    return dataclasses.replace(plan, refs=refs, ref_changes=changes)


def ref_change(
    name: str, row: dict[str, Any], *, added: bool = False, segment: int | None = None
) -> RefChange:
    return RefChange(
        added=added,
        name=name,
        segment=None,
        row=row,
        match="logicalId",
        value=row["logicalId"],
        segment_object_id=segment,
        owner=None,
    )


@pytest.mark.parametrize("name", sorted(APPEND_ONLY_REF_NAMES | PROCESSOR_REF_NAMES))
def test_a_row_of_a_processor_or_append_only_ref_is_never_applied_or_lost(name: str):
    module = qos(QOS_RAW, refs={name: [ROW1, ROW2]})
    plan = refs_plan(module, {name: [ROW2]}, ref_change(name, ROW1))
    stored = as_stored(module, plan.data, refs={name: [ROW1, ROW2]})
    assert check_refs(module, plan, stored) == ResponseCheck()


@pytest.mark.parametrize("name", sorted(APPEND_ONLY_REF_NAMES | PROCESSOR_REF_NAMES))
def test_a_difference_in_a_processor_or_append_only_ref_is_a_server_change(name: str):
    module = qos(QOS_RAW, refs={name: [ROW1, ROW2]})
    plan = refs_plan(module, {name: [ROW1, ROW2]})
    reissued = {**ROW1, "id": 901, "name": "other"}  # content is not compared
    row3 = {"id": 103, "enterpriseObjectId": 7, "logicalId": "a7"}
    stored = as_stored(module, plan.data, refs={name: [reissued, row3]})
    check = check_refs(module, plan, stored)
    assert (check.applied, check.not_applied, check.left_out) == ((), (), ())
    assert [(str(c.path), c.as_read, c.stored) for c in check.server_changes] == [
        (f'"{name}"[a6]', ROW2, MISSING),
        (f'"{name}"[a7]', MISSING, row3),
    ]


def test_removing_a_row_of_any_other_ref_is_not_applied_while_it_is_stored():
    module = qos(QOS_RAW, refs={"a": [ROW1, ROW2]})
    plan = refs_plan(module, {"a": [ROW2]}, ref_change("a", ROW1, segment=9))
    check = check_refs(module, plan, as_stored(module, plan.data))
    assert check.left_out == ()
    assert paths(check.not_applied) == ["a[a5,9]"]


def test_a_stored_module_read_without_its_refs_has_none_checked():
    module, plan = provider_cleared()
    stored = unset_provider_module(refs_loaded=False)
    assert check_refs(module, plan, stored) == ResponseCheck()


def test_the_stored_module_must_be_of_the_class_of_the_one_written():
    module = firewall_disabled()
    with pytest.raises(TypeError, match="QosConfigurationModule"):
        _ = check_module_write(module, plan_module(module), qos(QOS_RAW))


# --- the refs the VCO returns, checked by identity -----------------------------

PRIMARY_OWNER = 'segments["Global Segment"].dns.primary_provider.service'
UPDATE = "configuration/updateConfigurationModule"
VERSION = "configuration/getConfigurationModules"
CSS_SITE = "deviceSettings:css:site"
SITE_ROW: dict[str, Any] = {
    "id": 5,
    "configurationId": CONFIG,
    "moduleId": DS_ID,
    "enterpriseObjectId": 702,
    "ref": CSS_SITE,
    "segmentObjectId": None,
    "logicalId": DNS_B,
    "name": "dns-b",
    "type": "dns",
    "object": "NETWORK_SERVICE",
    "data": {},
}
"""A row of a name the VCO's processors manage, as read; the store's service 702 completes it the same way."""
DNS_A_IDENTITY = NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
SEG0_IDENTITY = SegmentIdentity("Global Segment", 0, SEG0_OBJECT, uuid.UUID(SEG0))


async def provider_set_over_a_store(
    mock_vco_client: MockVcoClientFactory, *, with_data_change: bool = True
) -> tuple[DeviceSettingsConfigurationModule, ModulePlan, ModuleStore, VcoClient]:
    """A device settings write of a new primary provider, over a store that holds the services."""
    module, plan = await provider_set(with_data_change=with_data_change)
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    return module, plan, store, await mock_vco_client(store)


def ref_paths(changes: Iterable[PlannedChange | ServerChange]) -> list[str]:
    return paths(c for c in changes if c.part == "refs")


async def test_a_provider_row_the_vco_stored_is_applied(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    write = await client.write_module(module, plan)
    (row,) = [c for c in write.applied if c.part == "refs"]
    assert str(row.path) == PRIMARY_ROW_PATH
    assert row.owner is not None and str(row.owner) == PRIMARY_OWNER
    assert (row.as_read, row.intended) == (MISSING, plan.ref_changes[0].row)
    assert write.server_changes == ()
    assert store.rows[module.id_]["refs"][PRIMARY]["logicalId"] == DNS_A


async def test_a_provider_row_the_vco_skipped_is_refs_dropped(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.add(701)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    (lost,) = caught.value.lost_edits
    assert lost.part == "refs" and PRIMARY in str(caught.value)
    assert lost.owner is not None and str(lost.owner) == PRIMARY_OWNER
    assert [c.part for c in caught.value.applied] == ["data"]
    assert caught.value.refs_as_read == module.raw_refs
    assert not caught.value.every_row_lost
    message = str(caught.value)
    assert "all of them are ref rows" in message
    assert "Check that the object of each row added exists" in message
    assert "The VCO kept a ref row" not in message
    assert (
        f"to set {PRIMARY_OWNER} again, send the patch "
        '{"segments": {"Global Segment": {"dns": {"primary_provider": '
        '{"service": "<the name or the logical ID of a service that exists>"}}}}} '
        "(error.module reads it as unset)"
    ) in message
    assert (
        "lost edits (not stored; `error.redo_patch()` redoes those it can, "
        "`error.not_redone()` lists the rest):"
    ) in message
    assert (
        "error.redo_patch() builds these patches with the real value (null for a clear)"
    ) in message


async def test_a_skipped_row_leaves_the_others_stored_and_sends_no_further_request(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.add(701)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    assert [method for method, _ in store.requests] == [VERSION, UPDATE]
    assert list(store.rows[module.id_]["refs"]) == [SEGMENT]
    assert caught.value.module is not None
    assert list(cast(dict[str, Any], caught.value.module.raw_refs)) == [SEGMENT]


async def test_every_row_lost_lists_them_all_and_carries_the_refs_as_read(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.update({SEG0_OBJECT, 701})
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert error.every_row_lost
    read_path = f'"{SEGMENT}"[{SEG0}]'
    assert ref_paths(error.lost_edits) == sorted([PRIMARY_ROW_PATH, read_path])
    read = next(c for c in error.lost_edits if str(c.path) == read_path)
    assert read.as_read == read.intended == segment_row(0, SEG0_OBJECT, SEG0)
    assert error.refs_as_read == module.raw_refs
    message = str(error)
    assert "took none of the ref rows this write sent" in message
    assert "error.refs_as_read" in message
    assert (
        f'{{"id": {module.id_}, "_update": {{"data": <the module\'s data as stored '
        'now>, "refs": <error.refs_as_read>}}'
    ) in message
    assert "update_configuration_module" in message


async def test_refs_as_read_leaves_out_an_append_only_row_present_as_read(
    mock_vco_client: MockVcoClientFactory,
):
    vnf = "deviceSettings:vnfs:edge"
    appended = {
        **service_row(vnf, DNS_A, 810, SEG0_OBJECT, SEG0),
        "type": "securityVnf",
    }
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0), vnf: appended},
    )
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    _ = current.merge_patch({"ntp": {"enabled": True}})
    resolvers = await table_registry(DNS_A_IDENTITY, SEG0_IDENTITY)
    plan = plan_module(module, current, resolvers=resolvers)
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    store.skipped_objects.update({SEG0_OBJECT, 701})
    client = await mock_vco_client(store)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert error.every_row_lost
    assert vnf in cast(dict[str, Any], module.raw_refs)
    assert error.refs_as_read == {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0)}
    message = str(error)
    for name in sorted(APPEND_ONLY_REF_NAMES):
        assert name in message
    assert "configuration/getConfiguration" in message
    assert f'{{"id": {module.configuration_id}, "with": ["modules"]}}' in message
    assert '"_update": {"data": <the module\'s data as stored now>' in message
    assert '"refs": <error.refs_as_read>}' in message
    assert '"logicalId"' not in message


async def test_a_row_sent_as_read_the_vco_dropped_is_a_server_change(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.add(SEG0_OBJECT)
    write = await client.write_module(module, plan)
    assert ref_paths(write.applied) == [PRIMARY_ROW_PATH]
    (dropped,) = write.server_changes
    assert dropped.part == "refs" and str(dropped.path) == f'"{SEGMENT}"[{SEG0}]'
    assert (dropped.as_read, dropped.stored) == (
        segment_row(0, SEG0_OBJECT, SEG0),
        MISSING,
    )


def module_with_a_processor_row() -> DeviceSettingsConfigurationModule:
    """A module with a provider stub and no provider row, and a row of ``deviceSettings:css:site``."""
    return ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0), CSS_SITE: SITE_ROW},
    )


async def edit_of_a_module_with_a_processor_row(
    mock_vco_client: MockVcoClientFactory,
) -> tuple[DeviceSettingsConfigurationModule, ModulePlan, ModuleStore, VcoClient]:
    module = module_with_a_processor_row()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    _ = current.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(
        module, current, resolvers=await table_registry(DNS_A_IDENTITY, SEG0_IDENTITY)
    )
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    return module, plan, store, await mock_vco_client(store)


async def test_processor_rows_are_not_compared(mock_vco_client: MockVcoClientFactory):
    module, plan, store, client = await edit_of_a_module_with_a_processor_row(
        mock_vco_client
    )
    assert plan.refs is not None and CSS_SITE in plan.refs
    write = await client.write_module(module, plan)  # the row is stored again, new ID
    site = store.rows[module.id_]["refs"][CSS_SITE]
    assert site["id"] != SITE_ROW["id"]
    assert write.server_changes == ()
    assert ref_paths(write.applied) == [PRIMARY_ROW_PATH]


async def test_a_change_of_a_processor_row_is_reported_and_is_not_a_lost_edit(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await edit_of_a_module_with_a_processor_row(
        mock_vco_client
    )

    def processor(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        new["refs"][CSS_SITE]["logicalId"] = DNS_A  # the processor chose another site

    store.on_store.append(processor)
    write = await client.write_module(module, plan)
    assert write.left_out == ()
    assert [(str(c.path), c.as_read is MISSING) for c in write.server_changes] == [
        (f'"{CSS_SITE}"[{DNS_B}]', False),
        (f'"{CSS_SITE}"[{DNS_A}]', True),
    ]
    assert ref_paths(write.applied) == [PRIMARY_ROW_PATH]


async def test_a_data_and_a_ref_failure_is_write_not_applied_listing_both(
    mock_vco_client: MockVcoClientFactory,
):
    module = unset_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    _ = current.merge_patch({"ntp": {"enabled": True, "source_interface": "GE1"}})
    plan = plan_module(module, current, resolvers=await table_registry(DNS_A_IDENTITY))
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    store.skipped_objects.add(701)

    def revert(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del new["data"]["ntp"]["sourceInterface"]

    store.on_store.append(revert)
    client = await mock_vco_client(store)
    with pytest.raises(WriteNotAppliedError) as caught:
        _ = await client.write_module(module, plan)
    assert type(caught.value) is WriteNotAppliedError
    assert sorted((c.part, str(c.path)) for c in caught.value.lost_edits) == [
        ("data", "ntp"),
        ("refs", PRIMARY_ROW_PATH),
    ]
    assert caught.value.applied == ()


def provider_cleared_with_a_data_edit() -> tuple[
    DeviceSettingsConfigurationModule, ModulePlan
]:
    module = primary_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, None)
    _ = current.merge_patch({"ntp": {"enabled": True}})
    return module, plan_module(module, current)


async def test_a_row_the_plan_removed_is_applied_when_it_is_gone(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = provider_cleared_with_a_data_edit()
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
    (removed,) = [c for c in write.applied if c.part == "refs"]
    assert (str(removed.path), removed.intended) == (PRIMARY_ROW_PATH, MISSING)
    assert write.server_changes == ()


async def test_a_row_the_plan_removed_that_is_still_stored_is_refs_dropped(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = provider_cleared_with_a_data_edit()
    store = store_of(module)
    store.objects.update(store_with_services().objects)

    def put_back(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        assert old is not None
        new["refs"][PRIMARY] = {**old["refs"][PRIMARY], "id": 99}

    store.on_store.append(put_back)
    client = await mock_vco_client(store)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    (lost,) = caught.value.lost_edits
    assert str(lost.path) == PRIMARY_ROW_PATH and lost.intended is MISSING
    assert lost.stored is not MISSING and lost.clash is False
    assert not caught.value.every_row_lost
    message = str(caught.value)
    assert "The VCO kept a ref row the plan removed" in message
    assert "Check that the object" not in message
    assert (
        f"to clear {PRIMARY_OWNER} again, send the patch "
        '{"segments": {"Global Segment": {"dns": {"primary_provider": '
        '{"service": null}}}}} (error.module reads it as set)'
    ) in message
    assert "to set " not in message


async def test_a_refused_refs_only_write_is_a_conflict(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = store_of(module, conditional_check=True)
    store.objects.update(store_with_services().objects)
    client = await mock_vco_client(
        changing_after_the_version_read(
            store,
            store,
            module.id_,
            lambda row: row["data"].update({"future": 2}),
        )
    )
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    assert not isinstance(caught.value, RefsDroppedError)
    assert caught.value.stated and caught.value.sent
    assert caught.value.detail == "the VCO refused the version"
    assert ref_paths(caught.value.lost_edits) == [PRIMARY_ROW_PATH]


async def test_a_refs_only_write_whose_row_is_skipped_is_a_conflict_naming_both_causes(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(
        mock_vco_client, with_data_change=False
    )
    store.skipped_objects.add(701)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert not isinstance(error, RefsDroppedError)
    assert not error.stated and error.applied == ()
    assert ref_paths(error.lost_edits) == [PRIMARY_ROW_PATH]
    assert "the module changed since it was read" in error.detail
    assert "the VCO would not take a new row because it cannot resolve its object" in (
        error.detail
    )


def a_row_with_no_owner() -> DeviceSettingsConfigurationModule:
    """A module with a primary provider and its row, and a backup provider row that nothing in the data names."""
    return primary_provider_module(
        {BACKUP: service_row(BACKUP, DNS_B, 702, SEG0_OBJECT, SEG0)}
    )


@pytest.mark.parametrize(
    "build", [a_row_with_no_owner, unset_provider_module], ids=["no-owner", "no-row"]
)
async def test_rows_that_disagree_as_read_raise_nothing_after_a_write(
    mock_vco_client: MockVcoClientFactory,
    build: Callable[[], DeviceSettingsConfigurationModule],
):
    module = build()
    current = copy_of_data(module)
    _ = current.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(module, current)
    assert plan.ref_changes == () and plan.refs is not None
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
    assert [c for c in write.applied if c.part == "refs"] == []
    assert write.server_changes == ()


def test_an_insert_checks_the_refs_it_planned():
    module = new_edge_module(ConfigurationModuleName.QOS, 10)
    assert isinstance(module, QosConfigurationModule)
    module.data.segments.append(QosSegment.model_validate(GLOBAL))
    plan = plan_module(module)
    assert plan.operation == "insert"
    row = {**primary_provider_row(), "id": None}
    added = ref_change(PRIMARY, row, added=True, segment=SEG0_OBJECT)
    plan = dataclasses.replace(plan, refs={PRIMARY: [row]}, ref_changes=(added,))
    held = {PRIMARY: [primary_provider_row()]}
    write = check_module_write(module, plan, as_stored(module, plan.data, refs=held))
    assert ref_paths(write.applied) == [PRIMARY_ROW_PATH]

    with pytest.raises(RefsDroppedError) as caught:
        _ = check_module_write(module, plan, as_stored(module, plan.data))
    assert caught.value.version == ""
    assert ref_paths(caught.value.lost_edits) == [PRIMARY_ROW_PATH]


# --- a lost provider edit is redone by setting its field ---------------------


def a_service_set(value: str | None) -> dict[str, object]:
    """The patch that sets the primary provider of the Global Segment to *value* (``None`` clears it)."""
    return {
        "segments": {"Global Segment": {"dns": {"primaryProvider": {"service": value}}}}
    }


def put_the_removed_row_back(new: dict[str, Any], old: dict[str, Any] | None) -> None:
    """A store hook: the VCO keeps the primary provider row the write removed."""
    assert old is not None
    new["refs"][PRIMARY] = {**old["refs"][PRIMARY], "id": 99}


async def provider_swapped_over_a_store(
    mock_vco_client: MockVcoClientFactory,
) -> tuple[DeviceSettingsConfigurationModule, ModulePlan, ModuleStore, VcoClient]:
    """A write that swaps ``dns-a`` for ``dns-b`` as the primary provider, and sets ``ntp``, over a store that holds the services."""
    module = primary_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_B))
    _ = current.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(
        module,
        current,
        resolvers=await table_registry(
            NetworkServiceIdentity("dns-b", uuid.UUID(DNS_B), 702, "dns")
        ),
    )
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    return module, plan, store, await mock_vco_client(store)


async def test_a_lost_provider_set_is_redone_by_its_field(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.add(701)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    patch = caught.value.redo_patch()
    assert patch == a_service_set(DNS_A)
    assert json.loads(json.dumps(patch)) == patch
    assert caught.value.not_redone() == ()
    assert caught.value.redo_patch(include_clashes=True) == patch


async def test_a_lost_provider_set_is_redone_on_a_conflict_too(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(
        mock_vco_client, with_data_change=False
    )
    store.skipped_objects.add(701)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    assert not isinstance(caught.value, RefsDroppedError)
    assert caught.value.redo_patch() == a_service_set(DNS_A)
    assert caught.value.not_redone() == ()


async def test_a_lost_provider_clear_is_redone_as_null(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = provider_cleared_with_a_data_edit()
    store = store_of(module)
    store.objects.update(store_with_services().objects)
    store.on_store.append(put_the_removed_row_back)
    client = await mock_vco_client(store)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    assert caught.value.redo_patch() == a_service_set(None)
    assert caught.value.not_redone() == ()


async def test_a_lost_provider_swap_is_one_set(mock_vco_client: MockVcoClientFactory):
    module, plan, store, client = await provider_swapped_over_a_store(mock_vco_client)
    store.skipped_objects.add(702)
    store.on_store.append(put_the_removed_row_back)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert sorted(c.intended is MISSING for c in error.lost_edits) == [False, True]
    assert error.redo_patch() == a_service_set(DNS_B)
    assert error.not_redone() == ()


async def test_a_lost_removal_beside_a_stored_add_is_a_set_not_a_clear(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, _, _ = await provider_swapped_over_a_store(mock_vco_client)
    both = {
        SEGMENT: segment_row(0, SEG0_OBJECT, SEG0),
        PRIMARY: [
            primary_provider_row(),  # the row the plan removed is kept
            service_row(PRIMARY, DNS_B, 702, SEG0_OBJECT, SEG0),  # and the new one
        ],
    }
    with pytest.raises(RefsDroppedError) as caught:
        _ = check_module_write(module, plan, as_stored(module, plan.data, refs=both))
    error = caught.value
    assert [c.intended is MISSING for c in error.lost_edits] == [True]
    assert [c.part for c in error.applied] == ["data", "refs"]
    assert error.redo_patch() == a_service_set(DNS_B)
    assert error.not_redone() == ()


async def test_the_redo_applied_to_the_module_as_stored_plans_the_same_row(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan, store, client = await provider_set_over_a_store(mock_vco_client)
    store.skipped_objects.add(701)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)
    stored = caught.value.module
    assert isinstance(stored, DeviceSettingsConfigurationModule)
    current = copy_of_data(stored)
    _ = current.merge_patch(caught.value.redo_patch())
    again = plan_module(
        stored,
        current,
        resolvers=await table_registry(DNS_A_IDENTITY, SEG0_IDENTITY),
    )

    def identities(changes: tuple[RefChange, ...]) -> list[tuple[str, object, object]]:
        return [(c.name, c.value, c.segment) for c in changes]

    assert identities(again.ref_changes) == identities(plan.ref_changes)
    assert identities(again.ref_changes) == [(PRIMARY, DNS_A, "Global Segment")]


async def lost_provider_and_dns_edit(
    stored_data: Callable[[dict[str, Any]], dict[str, Any]] = lambda raw: raw,
) -> WriteNotAppliedError:
    """The error of a write that sets a primary provider and the DNS source interface of the Global Segment, over a module as stored that *stored_data* makes of the module's data as read with its ``ntp`` change, and that holds neither of the others."""
    module = unset_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    _ = current.merge_patch(
        {
            "ntp": {"enabled": True},
            "segments": {"Global Segment": {"dns": {"source_interface": "GE1"}}},
        }
    )
    plan = plan_module(
        module,
        current,
        resolvers=await table_registry(
            NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
        ),
    )
    raw = stored_data(
        {
            **copy.deepcopy(cast(dict[str, Any], module.raw_data)),
            "ntp": plan.data["ntp"],
        }
    )
    with pytest.raises(WriteNotAppliedError) as caught:
        _ = check_module_write(module, plan, as_stored(module, raw))
    return caught.value


async def test_a_data_and_a_refs_edit_under_one_segment_are_one_object():
    error = await lost_provider_and_dns_edit()
    assert sorted((c.part, str(c.path)) for c in error.lost_edits) == [
        ("data", 'segments["Global Segment"].dns.sourceInterface'),
        ("refs", PRIMARY_ROW_PATH),
    ]
    patch = error.redo_patch()
    assert patch == {
        "segments": {
            "Global Segment": {
                "dns": {
                    "source_interface": "GE1",
                    "primaryProvider": {"service": DNS_A},
                }
            }
        }
    }
    assert error.not_redone() == ()


async def test_a_provider_is_set_in_the_segment_as_the_stored_list_names_it():
    def renamed(raw: dict[str, Any]) -> dict[str, Any]:
        raw["segments"][0]["segment"]["name"] = "Voice"
        return raw

    error = await lost_provider_and_dns_edit(renamed)
    assert error.redo_patch() == {
        "segments": {
            "Voice": {
                "dns": {
                    "source_interface": "GE1",
                    "primaryProvider": {"service": DNS_A},
                }
            }
        }
    }
    assert error.not_redone() == ()


async def test_a_provider_whose_segment_is_not_stored_is_not_redone():
    def no_segment(raw: dict[str, Any]) -> dict[str, Any]:
        raw["segments"] = []
        return raw

    error = await lost_provider_and_dns_edit(no_segment)
    assert sorted(c.part for c in error.not_redone()) == ["data", "refs"]
    assert error.redo_patch() == {}


async def test_a_provider_is_set_under_the_spelling_a_data_entry_used():
    error = await lost_provider_and_dns_edit()
    stub = Path(("segments", Key("Global Segment"), "dns", "primaryProvider", "ref"))
    error.lost_edits = tuple(
        dataclasses.replace(edit, path=stub, as_read="a", intended=PRIMARY, stored="a")
        if edit.part == "data"
        else edit
        for edit in error.lost_edits
    )
    patch = error.redo_patch()
    assert cast(dict[str, Any], patch)["segments"]["Global Segment"]["dns"] == {
        "primary_provider": {"ref": PRIMARY, "service": DNS_A}
    }
    assert isinstance(error.module, DeviceSettingsConfigurationModule)
    _ = error.module.data.merge_patch(patch)  # one spelling per field: no refusal


OTHER_SEGMENT = "99999999-9999-4999-8999-999999999999"


async def lost_provider_beside_ntp(
    stored_segments: list[dict[str, Any]], *, refs: object = MISSING
) -> WriteNotAppliedError:
    """The error of a write that sets a primary provider and ``ntp``, over a module as stored that holds ``ntp`` and the *stored_segments*, with the module's refs unless *refs* is given.

    No data edit is made in a segment, so the check records no stored part for
    the segment and the redo has to find it by identity.
    """
    module = unset_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    _ = current.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(module, current, resolvers=await table_registry(DNS_A_IDENTITY))
    raw = {**cast(dict[str, Any], module.raw_data), "ntp": plan.data["ntp"]}
    raw["segments"] = stored_segments
    with pytest.raises(WriteNotAppliedError) as caught:
        _ = check_module_write(module, plan, as_stored(module, raw, refs=refs))
    assert caught.value.stored_parts == {}
    return caught.value


async def test_a_provider_is_not_set_on_another_segment_that_took_the_name():
    error = await lost_provider_beside_ntp(
        [
            segment_entry(0, "Voice", SEG0, dns=dns(primary=True)),
            segment_entry(77, "Global Segment", OTHER_SEGMENT, dns=dns(primary=True)),
        ]
    )
    assert error.redo_patch() == {
        "segments": {"Voice": {"dns": {"primaryProvider": {"service": DNS_A}}}}
    }
    assert error.not_redone() == ()


async def test_a_provider_is_not_redone_when_no_stored_segment_is_the_one_edited():
    error = await lost_provider_beside_ntp(
        [segment_entry(77, "Global Segment", OTHER_SEGMENT, dns=dns(primary=True))]
    )
    assert [c.part for c in error.not_redone()] == ["refs"]
    assert error.redo_patch() == {}


async def test_a_segment_with_no_stored_row_is_found_by_its_logical_id():
    segments = [
        segment_entry(0, "Voice", SEG0, dns=dns(primary=True)),
        segment_entry(77, "Global Segment", OTHER_SEGMENT, dns=dns(primary=True)),
    ]
    no_rows: dict[str, Any] = {}
    error = await lost_provider_beside_ntp(segments, refs=no_rows)
    assert error.redo_patch() == {
        "segments": {"Voice": {"dns": {"primaryProvider": {"service": DNS_A}}}}
    }
    nothing_alike = [segment_entry(77, "Global Segment", OTHER_SEGMENT)]
    error = await lost_provider_beside_ntp(nothing_alike, refs=no_rows)
    assert PRIMARY_OWNER in {str(c.owner) for c in error.not_redone()}
    assert error.redo_patch() == {}


async def test_a_refs_entry_with_no_owner_is_not_redone():
    error = await lost_provider_and_dns_edit()
    error.lost_edits = tuple(
        dataclasses.replace(edit, owner=None) if edit.part == "refs" else edit
        for edit in error.lost_edits
    )
    assert [c.part for c in error.not_redone()] == ["refs"]
    assert "service" not in json.dumps(error.redo_patch())
