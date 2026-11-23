"""The outcome of one module write: what the VCO stored, against what the plan meant to change."""

import copy
import uuid
from collections.abc import Iterable
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    WriteNotAppliedError,
)
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.rules import FirewallInboundRule
from pyvelo.client.module_check import (
    APPEND_ONLY_REF_NAMES,
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
from pyvelo.core.rebase import Path
from pyvelo.core.response_check import (
    PathPattern,
    PlannedChange,
    ResponseCheck,
    ServerChange,
    check_response,
)
from pyvelo.testing import as_stored, make_module_json

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


def firewall_disabled() -> FirewallConfigurationModule:
    """The firewall module with one planned change: ``firewall_enabled`` off."""
    module = fw(RAW)
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
    module = fw(RAW)
    plan = plan_module(module, {**RAW, "future": 2})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {**RAW, "future": 3}))
    assert exc.value.lost_edits[0].clash
    assert "None is stored" in str(exc.value)


def test_partial_lists_applied_and_lost_apart():
    module = fw(RAW)
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
    module = fw(RAW)
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
    module = fw(RAW)
    plan = plan_module(module, {**RAW, "future": 2, "added": "x"})
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(module, plan, as_stored(module, {**RAW, "future": 3}))
    lines = str(exc.value).splitlines()
    assert "lost edits (not stored; redo these):" in lines
    assert (
        "  future: read 1, meant 2, stored 3  [clash: also changed on the VCO]" in lines
    )
    assert '  added: read (nothing), meant "x", stored (nothing)' in lines
    assert str(exc.value).count("[clash: also changed on the VCO]") == 1


def test_long_values_are_cut():
    module = fw(RAW)
    plan = plan_module(module, {**RAW, "future": "x" * 500})
    with pytest.raises(ModuleWriteError) as exc:
        _ = check_module_write(module, plan, as_stored(module, RAW))
    text = str(exc.value)
    assert all(len(line) <= 400 for line in text.splitlines())
    assert "…" in text


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


def test_ref_row_added_must_be_returned():
    module = qos(QOS_RAW, refs={"a": ROW1})
    # The data change shows the write was stored: a row it lacks is then not
    # applied, not a refused version.
    plan = plan_module(module, {**QOS_RAW, "future": 1}, refs={"a": [ROW1, ROW2]})
    reissued = {"a": [{**ROW1, "id": 201}, {**ROW2, "id": 202}]}
    write = check_module_write(
        module, plan, as_stored(module, plan.data, refs=reissued)
    )
    assert paths(c for c in write.applied if c.part == "refs") == ["a[6]"]

    without_row2 = {"a": [{**ROW1, "id": 201}]}
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = check_module_write(
            module, plan, as_stored(module, plan.data, refs=without_row2)
        )
    assert exc.value.lost_edits[0].part == "refs"
    assert paths(exc.value.lost_edits) == ["a[6]"]
    assert "refs a[" in str(exc.value)


def test_ref_row_removed_must_be_gone():
    module = qos(QOS_RAW, refs={"a": ROW1})
    plan = plan_module(module, refs={})
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, QOS_RAW))
    assert paths(exc.value.lost_edits) == ["a[5,9]"]
    assert exc.value.applied == ()


def test_refs_only_write_refused_is_a_conflict():
    module = qos(QOS_RAW, refs={"a": ROW1})
    plan = plan_module(module, refs={"a": [ROW1, ROW2]})
    assert plan.data == QOS_RAW
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, QOS_RAW))
    (lost,) = exc.value.lost_edits
    assert (lost.path, lost.part) == ("a[6]", "refs")
    assert (lost.as_read, lost.intended, lost.stored) == (MISSING, ROW2, MISSING)


def test_refs_the_vco_dropped_on_its_own():
    module = qos(QOS_RAW, refs={"a": [ROW1, ROW2]})
    plan = plan_module(module, {**QOS_RAW, "future": 1})
    assert plan.refs is None
    write = check_module_write(
        module, plan, as_stored(module, plan.data, refs={"a": [ROW1]})
    )
    (change,) = [c for c in write.server_changes if c.part == "refs"]
    assert change.path == "a[6]"
    assert change.as_read == ROW2
    assert change.stored is MISSING


def test_firewall_rule_id_revised_passes():
    module = fw(RAW)
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
    module = fw({**RAW, "inbound": [rule]})
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
    module = fw({**RAW, "inbound": [rule]})
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
    assert check_refs(MISSING, None, {"a": [ROW1]}) == ResponseCheck()


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
    module = fw(RAW)
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
    module = fw({**RAW, "inbound": [rule]})
    return module, plan_module(
        module, {**RAW, "inbound": [{**rule, "ruleLogicalId": "x2"}]}
    )


def ref_row_changed_inside() -> tuple[ConfigurationModuleBase, ModulePlan]:
    """A QoS module whose only planned change is inside a ref row that keeps its identity."""
    module = qos(QOS_RAW, refs={"a": [ROW1]})
    return module, plan_module(module, refs={"a": [{**ROW1, "logicalId": "b5"}]})


def append_only_row_removed() -> tuple[ConfigurationModuleBase, ModulePlan]:
    """A QoS module whose only planned change removes a row of an append-only ref."""
    module = qos(QOS_RAW, refs={ADDRESS_GROUP: [FIRST, SECOND]})
    return module, plan_module(module, refs={ADDRESS_GROUP: [SECOND]})


def test_a_change_of_data_is_checkable():
    module = firewall_disabled()
    assert has_checkable_change(module, plan_module(module)) is True


def test_a_ref_row_added_is_checkable():
    module = qos(QOS_RAW, refs={"a": [ROW1]})
    plan = plan_module(module, refs={"a": [ROW1, ROW2]})
    assert has_checkable_change(module, plan) is True


@pytest.mark.parametrize(
    "case",
    [ecmp_removed, owned_id_changed, ref_row_changed_inside, append_only_row_removed],
)
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


def test_refused_with_no_change_the_check_compares_is_a_conflict():
    module, plan = ref_row_changed_inside()
    stored = as_stored(module, QOS_RAW)
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, stored, answer="refused")
    error = exc.value
    assert (error.lost_edits, error.applied, error.left_out) == ((), (), ())
    assert error.module is stored
    assert error.plan is plan
    assert str(error).splitlines()[1].startswith("Nothing is listed as lost")


def test_refused_with_a_change_stored_and_one_left_out_as_read_is_a_conflict():
    rule = inbound("a", ruleLogicalId="x").model_dump(mode="json")
    module = fw({**RAW, "inbound": [rule]})
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
    module = fw({**RAW, "inbound": [rule]})
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
    module, plan = append_only_row_removed()
    with pytest.raises(ConfigConflictError) as exc:
        _ = check_module_write(module, plan, as_stored(module, QOS_RAW))
    assert exc.value.stated is False
    assert paths(exc.value.lost_edits) == ['"objectGroup:addressGroup"[7]']
    assert exc.value.left_out == ()


def test_a_counted_write_keeps_a_left_out_change_as_read_left_out():
    module, plan = append_only_row_removed()
    write = check_module_write(
        module, plan, as_stored(module, QOS_RAW), answer="counted"
    )
    assert isinstance(write, ModuleWrite)
    assert paths(write.left_out) == ['"objectGroup:addressGroup"[7]']
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


@pytest.mark.parametrize("name", sorted(APPEND_ONLY_REF_NAMES))
def test_removing_a_row_of_an_append_only_ref_is_left_out(name: str):
    check = check_refs({name: [ROW1, ROW2]}, {name: [ROW2]}, {name: [ROW1, ROW2]})
    assert [(c.part, c.as_read) for c in check.left_out] == [("refs", ROW1)]
    assert check.not_applied == ()


def test_removing_a_row_of_any_other_ref_is_not_applied_while_it_is_stored():
    check = check_refs({"a": [ROW1, ROW2]}, {"a": [ROW2]}, {"a": [ROW1, ROW2]})
    assert check.left_out == ()
    assert paths(check.not_applied) == ["a[5,9]"]


def test_the_stored_module_must_be_of_the_class_of_the_one_written():
    module = firewall_disabled()
    with pytest.raises(TypeError, match="QosConfigurationModule"):
        _ = check_module_write(module, plan_module(module), qos(QOS_RAW))
