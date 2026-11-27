"""A write error names fields, items and the module as the plan's text does.

The lines of ``ModuleWriteError.render()`` show each path as the plan of the
write shows it; the lists of the error (``lost_edits``, ``applied``,
``server_changes``) keep the paths the check made, in the VCO's keys.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

import pytest
from hub_state import OUT_OF_THE_RULE, edit, guest_backhaul_state, rule_uses_hub
from profile_state import module_store_pipeline
from pydantic import MISSING
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    RefsDroppedError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.module_check import ModuleWrite, check_module_write
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.config import add, at, move
from pyvelo.config.errors import SaveStepError
from pyvelo.config.pipeline import (
    ConfigPipeline,
    _how_it_differs,  # pyright: ignore[reportPrivateUsage]  # the refusal's text is built here
)
from pyvelo.core.changes import patch_of
from pyvelo.core.rebase import Key, Path, RawChange
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import MockVcoClient, as_stored
from refs_helpers import DNS_A, DS_ID
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    SEGMENT,
    firewall,
    fw_rule,
    make_state,
)
from test_profile_segment_save import (  # pyright: ignore[reportImplicitRelativeImport]
    global_only,
    guest_stripped,
)
from test_profile_segment_save import (  # pyright: ignore[reportImplicitRelativeImport]
    rule as template_rule,
)
from test_save_refs import (  # pyright: ignore[reportImplicitRelativeImport]
    loaded_profile,
    loaded_with_css,
    provider_patch,
    set_css,
)
from test_shown_paths_round_trip import (  # pyright: ignore[reportImplicitRelativeImport]
    forbidden_names,
    vco_names,
)
from test_shown_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    LAN,
    SECRET,
    device_settings,
    lan_plan,
    lan_state,
    net,
    rule_with_id,
)
from test_write_back_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    DENY,
    MODULE,
    OUTBOUND,
    other_writer_edit,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pyvelo.client.derived_refs import RefChange
    from pyvelo.client.schemas.configuration import (
        AnyStoredModule,
        ConfigurationModuleBase,
        DeviceSettingsConfigurationModule,
    )
    from pyvelo.config.state import ConfigState

HUB = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
SERVICE_ROW = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ROW = f'"deviceSettings:backHaulEdge"[{SERVICE_ROW},502]'
PORTS = {
    "segments": {
        "Guest": {"rules": {"Web": {"match": {"dport_low": 443, "dport_high": 443}}}}
    }
}
LOST = (
    "lost edits (not stored; `error.redo_patch()` redoes those it can, "
    "`error.not_redone()` lists the rest):"
)


def failed(
    module: ConfigurationModuleBase, plan: ModulePlan, stored: object
) -> ModuleWriteError:
    """The error of the write of *plan* that the VCO stored as *stored*."""
    with pytest.raises(ModuleWriteError) as caught:
        _ = check_module_write(module, plan, as_stored(module, stored))
    return caught.value


def lines_of(error: ModuleWriteError, heading: str) -> list[str]:
    """The lines of the error's text under *heading*, up to the next heading."""
    lines = error.render().splitlines()
    rest = lines[lines.index(heading) + 1 :]
    below: list[str] = []
    for line in rest:
        if not line.startswith("  "):
            break
        below.append(line)
    return below


def as_read(state: ConfigState) -> dict[str, Any]:
    """A copy of the device settings of *state* as read."""
    return raw_copy(state.module("deviceSettings"))


def raw_copy(module: ConfigurationModuleBase) -> dict[str, Any]:
    """A copy of the data of *module* as read."""
    return cast("dict[str, Any]", copy.deepcopy(module.raw_data))


async def three_networks() -> ConfigState:
    """VLAN 10 ``Users``, VLAN 20 ``Voice`` and VLAN 30 ``Guests``."""
    return await lan_state(net(10, "Users"), net(20, "Voice"), net(30, "Guests"))


def lan_error(state: ConfigState, stored: object) -> ModuleWriteError:
    return failed(state.module("deviceSettings"), lan_plan(state), stored)


async def ports_and_a_hub_lost() -> tuple[ModuleWriteError, ModulePlan]:
    """Two ports and a hub of a rule's backhaul list; the VCO stored one port.

    The error of that write, and its plan.
    """
    state = await rule_uses_hub()
    await edit(state, "QOS", PORTS)
    await edit(state, "QOS", OUT_OF_THE_RULE)
    (plan,) = [m for m in state.plan().modules if m.name == "QOS"]
    module = state.module("QOS")
    stored = raw_copy(module)
    (guest,) = [s for s in stored["segments"] if s["segment"]["name"] == "Guest"]
    guest["rules"][0]["match"].update({"dport_high": 443, "serverAdded": "x"})
    return failed(module, plan, stored), plan


async def test_a_lost_edit_reads_as_the_plan_s_line_for_it() -> None:
    error, plan = await ports_and_a_hub_lost()

    assert isinstance(error, WriteNotAppliedError)
    assert error.module_name == "business_policy"
    hubs = (
        "segments[Guest].rules[Web].action.edge_to_edge_route_action.route_cfg."
        "backhaul_edges[Hub-A]"
    )
    text = error.render()
    assert text.splitlines()[0] == (
        "Configuration module 11 (business_policy): 3 of 4 planned changes are "
        "not stored as sent"
    )
    port, hub, row = lines_of(error, LOST)
    assert port == (
        "  segments[Guest].rules[Web].match.destination_port_low: "
        "read -1, meant 443, stored -1"
    )
    assert hub.startswith(f'  {hubs}: read {{"edge_id": 31, "edge_logical_id": "{HUB}"')
    assert ", meant (nothing), stored {" in hub
    assert row == (
        "  refs deviceSettings:backHaulEdge (Guest) "
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa: "
        'read "***", meant (nothing), stored "***"'
    )
    assert (
        "  - refs deviceSettings:backHaulEdge (Guest): "
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ) in plan.ref_lines()
    assert _how_it_differs(error, plan).endswith(
        "refs deviceSettings:backHaulEdge (Guest) aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    assert lines_of(error, "already stored:") == [
        "  segments[Guest].rules[Web].match.destination_port_high"
    ]
    # A key the model does not declare is kept as stored, below the named rule.
    assert lines_of(error, "stored otherwise than sent:") == [
        '  segments[Guest].rules[Web].match.serverAdded: read (nothing), stored "x"'
    ]
    assert "edgeLogicalId" not in text
    assert "dport" not in text
    # Each line has the path of the plan's line for that change.
    plan_lines = plan.render().splitlines()
    assert f"  - {hubs}" in plan_lines
    assert any(
        line.startswith("  ~ segments[Guest].rules[Web].match.destination_port_low: ")
        for line in plan_lines
    )
    # The path of a lost edit is one ``apply`` takes, with the error's module.
    fresh = await rule_uses_hub()
    _ = await ConfigPipeline(client=None).apply(
        fresh,
        module=error.module_name,
        path=port.partition(": ")[0].strip(),
        patch=443,
    )
    (again,) = [m for m in fresh.plan().modules if m.name == "QOS"]
    assert again.render().splitlines()[1:2] == [
        "  ~ segments[Guest].rules[Web].match.destination_port_low: -1 -> 443"
    ]
    # The lists keep the paths of the check; the redo patch names the hub as
    # the stored list shows it.
    assert [str(change.path) for change in error.lost_edits] == [
        "segments[Guest].rules[Web].match.dport_low",
        (
            "segments[Guest].rules[Web].action.edge2EdgeRouteAction.routeCfg."
            f"backhaulEdges[{HUB}]"
        ),
        ROW,
    ]
    assert error.redo_patch() == {
        "segments": {
            "Guest": {
                "rules": {
                    "Web": {
                        "match": {"destination_port_low": 443},
                        "action": {
                            "edge_to_edge_route_action": {
                                "route_cfg": {
                                    "backhaul_edges": {"Hub-A": {"_op": "delete"}}
                                }
                            }
                        },
                    }
                }
            }
        }
    }


async def test_a_lost_edit_inside_a_renamed_item_is_under_the_new_name() -> None:
    state = await three_networks()
    users = state.profile.device_settings.lan.networks[0]
    users.name = "Staff"
    users.cidr_prefix = 25

    error = lan_error(state, as_read(state))

    assert isinstance(error, ConfigConflictError)
    assert error.module_name == "device_settings"
    assert error.render().startswith("Configuration module 10 (device_settings): ")
    assert lines_of(error, LOST) == [
        '  lan.networks[Staff].name: read "Users", meant "Staff", stored "Users"',
        "  lan.networks[Staff].cidr_prefix: read 24, meant 25, stored 24",
    ]
    assert [str(change.path) for change in error.lost_edits] == [
        "lan.networks[10].name",
        "lan.networks[10].cidrPrefix",
    ]


async def test_a_lost_removal_is_named_as_read_with_its_keys_by_attribute() -> None:
    state = await three_networks()
    del state.profile.device_settings.lan.networks[1]

    error = lan_error(state, as_read(state))

    (line,) = lines_of(error, LOST)
    assert line.startswith(
        '  lan.networks[Voice]: read {"cidr_ip": "10.20.0.0", "cidr_prefix": 24, '
        '"name": "Voice", "segment_id": 0, '
    )
    assert "cidrIp" not in line
    (lost,) = error.lost_edits
    assert lost.path == "lan.networks[20]"
    assert isinstance(lost.as_read, dict)
    assert "cidrIp" in lost.as_read


async def test_a_lost_order_names_its_items_as_the_plan_s_line_does() -> None:
    state = await three_networks()
    state.profile.device_settings.lan.networks.reverse()
    plan = lan_plan(state)
    order = "lan.networks: order: Guests first; Voice after Guests"
    assert f"  ~ {order}" in plan.render().splitlines()

    error = lan_error(state, as_read(state))

    assert lines_of(error, LOST) == [
        (
            f'  {order}: read ["Users", "Voice", "Guests"], '
            'meant ["Guests", "Voice", "Users"], stored ["Users", "Voice", "Guests"]'
        )
    ]
    (lost,) = error.lost_edits
    assert (lost.as_read, lost.stored) == (["10", "20", "30"], ["10", "20", "30"])


async def test_a_lost_added_item_has_its_place_after_a_named_item() -> None:
    state = await three_networks()
    state.profile.device_settings.lan.networks.append(
        type(state.profile.device_settings.lan.networks[0]).model_validate(
            net(40, "Lab")
        )
    )

    error = lan_error(state, as_read(state))

    (line,) = lines_of(error, LOST)
    assert line.startswith(
        '  lan.networks[Lab] (after Guests): read (nothing), meant {"advertise": true, '
        '"angular_update": '
    )
    assert line.endswith(", stored (nothing)")
    (lost,) = error.lost_edits
    assert lost.path == "lan.networks[40]"


async def two_prefixes() -> ConfigState:
    """Three networks, and an edit of the prefix of the first two."""
    state = await three_networks()
    networks = state.profile.device_settings.lan.networks
    networks[0].cidr_prefix = 25
    networks[1].cidr_prefix = 26
    return state


async def test_what_the_vco_stored_otherwise_is_named_where_it_is() -> None:
    """The second prefix is stored; the VCO has a key and three networks of its own.

    Two of its networks share a VLAN ID, so only their positions in the stored
    list name them: positions that are other networks' in the plan's data.
    """
    state = await two_prefixes()
    stored = as_read(state)
    networks = stored["lan"]["networks"]
    networks[1]["cidrPrefix"] = 26
    networks[0]["serverAdded"] = "x"
    networks[:0] = [net(40, "Extra"), net(40, "Other")]
    networks.append(net(50, "Keyed"))

    error = lan_error(state, stored)

    assert lines_of(error, LOST) == [
        "  lan.networks[Users].cidr_prefix: read 24, meant 25, stored 24"
    ]
    assert lines_of(error, "already stored:") == ["  lan.networks[Voice].cidr_prefix"]
    key, first, second, keyed = lines_of(error, "stored otherwise than sent:")
    assert key == '  lan.networks[Users].serverAdded: read (nothing), stored "x"'
    assert first.startswith(
        '  lan.networks[#0]: read (nothing), stored {"cidr_ip": "10.40.0.0", '
        '"cidr_prefix": 24, "name": "Extra", '
    )
    assert second.startswith("  lan.networks[#1]: read (nothing), stored {")
    assert '"name": "Other"' in second
    assert keyed.startswith(
        '  lan.networks[50]: read (nothing), stored {"cidr_ip": "10.50.0.0", '
    )
    assert [str(change.path) for change in error.server_changes] == [
        "lan.networks[10].serverAdded",
        "lan.networks[#0]",
        "lan.networks[#1]",
        "lan.networks[50]",
    ]


async def test_an_order_the_vco_stored_otherwise_names_the_items() -> None:
    state = await two_prefixes()
    stored = as_read(state)
    networks = stored["lan"]["networks"]
    networks[1]["cidrPrefix"] = 26
    networks.insert(0, networks.pop())

    error = lan_error(state, stored)

    assert lines_of(error, "stored otherwise than sent:") == [
        (
            '  lan.networks: read ["Users", "Voice", "Guests"], '
            'stored ["Guests", "Users", "Voice"]'
        )
    ]
    (change,) = error.server_changes
    assert (change.as_read, change.stored) == (["10", "20", "30"], ["30", "10", "20"])


def ntp_and_a_vlan() -> tuple[ConfigurationModuleBase, ModulePlan]:
    """An NTP server added with a key, and a VLAN put on ``GE1``."""
    module = device_settings(LAN)
    keys = [{"keyNumber": 1, "key": SECRET}]
    _ = module.data.merge_patch(
        {"ntp_server": {"enabled": True, "authentication": "MD5", "keys": keys}}
    )
    module.data.lan.interfaces[0].vlan_ids = [10]
    return module, plan_module(module)


def test_a_secret_in_a_lost_object_is_hidden_under_its_attribute_name() -> None:
    module, plan = ntp_and_a_vlan()

    error = failed(module, plan, raw_copy(module))

    text = error.render()
    assert SECRET not in text
    (line,) = [line for line in lines_of(error, LOST) if "ntp_server" in line]
    assert line.startswith(
        '  ntp_server: read (nothing), meant {"authentication": "MD5", '
        '"enabled": true, "keys": [{"key": "***", "key_number":'
    )
    assert line.endswith(", stored (nothing)")
    (lost,) = [change for change in error.lost_edits if change.path == "ntpServer"]
    assert isinstance(lost.intended, dict)
    assert lost.intended["keys"] == [{"keyNumber": 1, "key": SECRET}]


def test_below_a_value_stored_whole_an_item_keeps_its_position() -> None:
    """The NTP server is stored with a key of the VCO's inside its first key."""
    module, plan = ntp_and_a_vlan()
    stored = copy.deepcopy(plan.data)
    stored["ntpServer"]["keys"][0]["serverAdded"] = "x"
    del stored["lan"]["interfaces"][0]["vlanIds"]

    error = failed(module, plan, stored)

    assert lines_of(error, "already stored:") == ["  ntp_server"]
    assert lines_of(error, "stored otherwise than sent:") == [
        '  ntp_server.keys[#0].serverAdded: read (nothing), stored "x"'
    ]
    assert [str(change.path) for change in error.server_changes] == [
        "ntpServer.keys[#0].serverAdded"
    ]


def rule_of_id(name: str, number: int) -> dict[str, Any]:
    """A QoS rule *name* with a logical ID of its own, made of *number*."""
    rule = rule_with_id(name, 80 + number)
    rule["ruleLogicalId"] = f"{number:08d}-0000-4000-8000-000000000000"
    return rule


async def test_below_a_list_stored_whole_no_position_is_read_as_an_item_as_read() -> (
    None
):
    """Rule ``a`` is renamed ``c``, and a new rule ``a`` is added after ``b``.

    The added rule has the name of one as read, so the list is planned, and
    checked, whole: the VCO's key in each rule is at the rule's position as
    planned. The rule as read at position 0 is ``a``, and the plan has
    another ``a`` at position 2: position 0 is neither's name.
    """
    state = await guest_backhaul_state(rule_of_id("a", 1), rule_of_id("b", 2))
    rules = state.profile.qos.segments[1].rules
    rules[0].name = "c"
    rules.append(type(rules[0]).model_validate(rule_of_id("a", 3)))
    (plan,) = [m for m in state.plan().modules if m.name == "QOS"]
    assert [str(change.path) for change in plan.changes] == ["segments[Guest].rules"]
    module = state.module("QOS")
    stored = copy.deepcopy(plan.data)
    (guest,) = [s for s in stored["segments"] if s["segment"]["name"] == "Guest"]
    assert [rule["name"] for rule in guest["rules"]] == ["c", "b", "a"]
    for position, rule in enumerate(guest["rules"]):
        rule["serverAdded"] = position
    write = check_module_write(module, plan, as_stored(module, stored))

    error = ModuleWriteError(
        11,
        "v1",
        module_name="QOS",
        plan=plan,
        applied=write.applied,
        server_changes=write.server_changes,
    )

    assert lines_of(error, "already stored:") == ["  segments[Guest].rules"]
    assert lines_of(error, "stored otherwise than sent:") == [
        f"  segments[Guest].rules[#{position}].serverAdded: "
        f"read (nothing), stored {position}"
        for position in range(3)
    ]


def test_an_error_without_a_plan_keeps_each_path_as_written() -> None:
    lost = PlannedChange(Path.parse("lan.networks[10].cidrPrefix"), 24, 25, 24)
    extra = ServerChange(Path.parse("lan.networks[#0]"), MISSING, {"cidrIp": "x"})
    error = ModuleWriteError(
        10,
        "v1",
        module_name="deviceSettings",
        lost_edits=[lost],
        server_changes=[extra],
    )
    assert error.module_name == "device_settings"
    assert error.render().splitlines() == [
        "Configuration module 10 (device_settings): the write is not stored as planned",
        LOST,
        "  lan.networks[10].cidrPrefix: read 24, meant 25, stored 24",
        "stored otherwise than sent:",
        '  lan.networks[#0]: read (nothing), stored {"cidrIp": "x"}',
    ]


def test_a_plan_made_from_a_mapping_names_the_fields_of_a_lost_edit() -> None:
    """It has no data to find an item in: the item keeps its key."""
    module = device_settings({**LAN, "lan": {**LAN["lan"], "networks": [net(10, "U")]}})
    data = raw_copy(module)
    data["lan"]["networks"][0]["cidrPrefix"] = 25
    plan = plan_module(module, data)
    assert plan.planned is None

    error = failed(module, plan, raw_copy(module))

    assert lines_of(error, LOST) == [
        "  lan.networks[10].cidr_prefix: read 24, meant 25, stored 24"
    ]


async def test_a_write_not_checked_lists_its_changes_as_the_plan_does() -> None:
    state = await three_networks()
    networks = state.profile.device_settings.lan.networks
    networks[0].name = "Staff"
    networks.reverse()
    plan = lan_plan(state)

    error = WriteOutcomeUnknownError(
        10, "v1", "no row", answer="counted", module_name="deviceSettings", plan=plan
    )

    assert error.module_name == "device_settings"
    lines = error.render().splitlines()
    assert lines[0].startswith("Configuration module 10 (device_settings): ")
    assert lines_of(error, "planned, not checked:")[:2] == [
        '  lan.networks[Staff].name: read "Users", meant "Staff"',
        (
            "  lan.networks: order: Guests first; Voice after Guests: "
            'read ["Staff", "Voice", "Guests"], meant ["Guests", "Voice", "Staff"]'
        ),
    ]


async def test_the_writes_before_a_failed_one_are_named_in_python_names() -> None:
    state = await two_prefixes()
    module = state.module("deviceSettings")
    error = lan_error(state, as_read(state))
    error.completed = (
        ModuleWrite(
            name="QOS", operation="update", module=module, plan=lan_plan(state)
        ),
    )
    assert error.render().splitlines()[-1] == (
        "written before this one: business_policy (update)"
    )


@pytest.mark.parametrize(
    ("given", "held"),
    [
        ("QOS", "business_policy"),
        ("deviceSettings", "device_settings"),
        ("business_policy", "business_policy"),
        ("", ""),
    ],
)
def test_a_write_error_holds_the_python_name_of_its_module(
    given: str, held: str
) -> None:
    error = ModuleWriteError(7, "v1", module_name=given)
    assert error.module_name == held
    assert copy.copy(error).module_name == held
    label = f"7 ({held})" if held else "7"
    assert str(error) == (
        f"Configuration module {label}: the write is not stored as planned"
    )


def test_a_save_step_error_holds_the_python_name_of_its_module() -> None:
    error = SaveStepError("timeout", module_name="deviceSettings")
    assert error.module_name == "device_settings"
    assert copy.copy(error).module_name == "device_settings"
    assert "the write of the module device_settings, which failed" in str(error)
    assert SaveStepError("timeout").module_name is None


def test_a_refusal_of_revert_names_a_refs_entry_as_a_write_error_does() -> None:
    """A row of refs is no path of the data: ``refs`` goes before it."""
    _, plan = ntp_and_a_vlan()
    row = Path(("deviceSettings:backHaulEdge", Key(("aaaa", 502))))
    lost = PlannedChange(row, MISSING, {"id": 1}, MISSING, part="refs")
    data = PlannedChange(Path.parse("lan.interfaces[GE1].vlanIds"), [], [10], [])
    error = ModuleWriteError(
        10, "v1", module_name="deviceSettings", plan=plan, lost_edits=[lost, data]
    )
    entry = 'refs "deviceSettings:backHaulEdge"[aaaa,502]'
    assert f"  {entry}: " in error.render()
    assert _how_it_differs(error, plan) == (
        f"it differs at {entry}, lan.interfaces[GE1].vlan_ids"
    )


HUB_ROW = Path(("deviceSettings:backHaulEdge", Key((SERVICE_ROW, 502))))
HUB_ROW_NAMED = f"refs deviceSettings:backHaulEdge (Guest) {SERVICE_ROW}"


async def hub_out_of_the_rule() -> tuple[ModulePlan, RefChange]:
    """The plan that takes Hub-A out of the rule ``Web``, and the row it removes."""
    state = await rule_uses_hub()
    await edit(state, "QOS", OUT_OF_THE_RULE)
    (plan,) = [m for m in state.plan().modules if m.name == "QOS"]
    (removed,) = plan.ref_changes
    return plan, removed


def qos_error(
    plan: ModulePlan | None,
    *,
    lost_edits: Sequence[PlannedChange] = (),
    server_changes: Sequence[ServerChange] = (),
) -> ModuleWriteError:
    """A write error of the QoS module 11 with *plan* and these lists."""
    return ModuleWriteError(
        11,
        "v1",
        module_name="QOS",
        plan=plan,
        lost_edits=lost_edits,
        server_changes=server_changes,
    )


async def test_a_ref_row_the_plan_removes_is_named_only_with_the_plan() -> None:
    plan, removed = await hub_out_of_the_rule()
    kept = PlannedChange(HUB_ROW, removed.row, MISSING, removed.row, part="refs")

    with_plan = qos_error(plan, lost_edits=[kept])
    without = qos_error(None, lost_edits=[kept])

    assert str(HUB_ROW) == ROW
    assert lines_of(with_plan, LOST) == [
        f'  {HUB_ROW_NAMED}: read "***", meant (nothing), stored "***"'
    ]
    assert _how_it_differs(with_plan, plan) == f"it differs at {HUB_ROW_NAMED}"
    # An error with no plan has nothing to name the row by.
    assert lines_of(without, LOST) == [
        f'  refs {ROW}: read "***", meant (nothing), stored "***"'
    ]


async def test_a_ref_row_only_the_vco_changed_keeps_its_path() -> None:
    """A server change is no row of the plan, though it holds the row of one."""
    plan, removed = await hub_out_of_the_rule()
    dropped = ServerChange(HUB_ROW, removed.row, MISSING, part="refs")

    error = qos_error(plan, server_changes=[dropped])

    assert plan.ref_change_of(dropped) is None
    assert lines_of(error, "stored otherwise than sent:") == [
        f'  refs {ROW}: read "***", stored (nothing)'
    ]


async def test_a_refs_entry_that_is_no_row_of_the_plan_keeps_its_path() -> None:
    """A row sent as read, and a row the plan neither adds nor removes."""
    plan, removed = await hub_out_of_the_rule()
    as_read = PlannedChange(HUB_ROW, removed.row, removed.row, MISSING, part="refs")
    other = PlannedChange(HUB_ROW, MISSING, {"logicalId": HUB}, MISSING, part="refs")

    error = qos_error(plan, lost_edits=[as_read, other])

    assert plan.ref_change_of(as_read) is None
    assert plan.ref_change_of(other) is None
    assert lines_of(error, LOST) == [
        f'  refs {ROW}: read "***", meant "***", stored (nothing)',
        f'  refs {ROW}: read (nothing), meant "***", stored (nothing)',
    ]
    assert _how_it_differs(error, plan) == f"it differs at refs {ROW}"


async def test_a_row_added_and_the_same_row_removed_are_told_apart() -> None:
    plan, removed = await hub_out_of_the_rule()
    added = replace(removed, added=True)
    both = replace(plan, ref_changes=(removed, added))
    gone = PlannedChange(HUB_ROW, removed.row, MISSING, MISSING, part="refs")
    made = PlannedChange(HUB_ROW, MISSING, removed.row, MISSING, part="refs")

    assert both.ref_change_of(gone) is removed
    assert both.ref_change_of(made) is added
    assert both.ref_entry(gone) == both.ref_entry(made) == HUB_ROW_NAMED


def networks_module(*networks: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    """Device settings read with the LAN *networks* and three interfaces."""
    return device_settings({**LAN, "lan": {**LAN["lan"], "networks": list(networks)}})


def test_an_order_the_vco_changed_names_only_the_items_that_were_sent() -> None:
    """The plan edits two interfaces; the VCO reorders the networks and adds two.

    Its own two share a VLAN ID, so the check names them by their positions in
    the stored list, in their own lines and in the stored order: positions
    that are other networks' in the plan's data.
    """
    module = networks_module(net(10, "Users"), net(20, "Voice"), net(30, "Guests"))
    module.data.lan.interfaces[0].vlan_ids = [10]
    module.data.lan.interfaces[1].vlan_ids = [20]
    plan = plan_module(module)
    stored = copy.deepcopy(plan.data)
    del stored["lan"]["interfaces"][1]["vlanIds"]
    users, voice, guests = stored["lan"]["networks"]
    stored["lan"]["networks"] = [
        net(40, "Extra"),
        net(40, "Other"),
        guests,
        users,
        voice,
    ]

    error = failed(module, plan, stored)

    (order,) = [c for c in error.server_changes if c.path == "lan.networks"]
    assert order.as_read == ["10", "20", "30"]
    assert order.stored == ["#0", "#1", "30", "10", "20"]
    first, second, shown_order = lines_of(error, "stored otherwise than sent:")
    assert first.startswith("  lan.networks[#0]: read (nothing), stored {")
    assert '"name": "Extra"' in first
    assert second.startswith("  lan.networks[#1]: read (nothing), stored {")
    assert shown_order == (
        '  lan.networks: read ["Users", "Voice", "Guests"], '
        'stored ["#0", "#1", "Guests", "Users", "Voice"]'
    )


def test_a_stored_position_is_not_the_sent_item_the_vco_dropped_at_it() -> None:
    """``A`` and ``B`` share a VLAN ID, so the check names them ``#0`` and ``#1``.

    The stored list lacks ``A`` and has a network of the VCO's own first,
    which only its position there names: ``#0`` again. The entry that says
    ``A`` is gone tells the two apart.
    """
    module = networks_module(
        net(10, "A"), net(10, "B", 1), net(20, "Voice"), net(30, "Guests")
    )
    module.data.lan.interfaces[0].vlan_ids = [10]
    plan = plan_module(module)
    networks = Path.parse("lan.networks")
    error = ModuleWriteError(
        10,
        "v1",
        module_name="deviceSettings",
        plan=plan,
        server_changes=[
            ServerChange(networks.child(0), net(10, "A"), MISSING),
            ServerChange(networks.child(0), MISSING, net(40, "Own")),
            ServerChange(networks, ["#0", "#1", "20", "30"], ["#0", "30", "#1", "20"]),
        ],
    )
    gone, own, order = lines_of(error, "stored otherwise than sent:")
    assert gone.startswith("  lan.networks[A]: read {")
    assert gone.endswith(", stored (nothing)")
    assert own.startswith("  lan.networks[#0]: read (nothing), stored {")
    assert order == (
        '  lan.networks: read ["A", "B", "Voice", "Guests"], '
        'stored ["#0", "Guests", "B", "Voice"]'
    )


@pytest.mark.parametrize("text", ["a b", "", "x.y"])
def test_a_text_that_is_no_item_name_is_printed_as_it_is(text: str) -> None:
    """An entry made by hand: the message prints what it holds."""
    module = networks_module(net(10, "Users"), net(20, "Voice"))
    module.data.lan.interfaces[0].vlan_ids = [10]
    order = ServerChange(Path.parse("lan.networks"), ["10", text], [text, "10"])
    error = ModuleWriteError(
        10,
        "v1",
        module_name="deviceSettings",
        plan=plan_module(module),
        server_changes=[order],
    )
    assert lines_of(error, "stored otherwise than sent:") == [
        f'  lan.networks: read ["Users", "{text}"], stored ["{text}", "Users"]'
    ]


# ---------------------------------------------------------------------------
# The redo patch, and what it leaves out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lost:
    """A write that lost edits: its error, and the state to redo them on.

    The state holds the module as stored, which is the error's ``module``.
    """

    error: ModuleWriteError
    state: ConfigState


async def firewall_conflict(
    raw: dict[str, Any],
    patch: dict[str, Any],
    other: Callable[[dict[str, Any]], None],
    *,
    segment: str = SEGMENT,
    profile: dict[str, Any] | None = None,
) -> Lost:
    """A save of *patch* to the outbound rules of *raw*, after *other* wrote the row."""
    state = make_state({"firewall": raw}, {"firewall": profile or firewall()})
    client = MockVcoClient.for_state(state, conditional_check=True)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state, module="firewall", segment=segment, path="outbound", patch=patch
    )
    client.store.other_writer(MODULE, other)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)
    return Lost(caught.value, state)


def stored_outbound(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The outbound rules of the first segment of a firewall *row* of the store."""
    return row["data"]["segments"][0]["outbound"]


async def a_place_and_a_move() -> Lost:
    """A rule added first, an edit and a move; another writer's key is stored."""
    raw = firewall(
        fw_rule("block-ssh", ruleLogicalId="l1"),
        fw_rule("allow-branch-erp", ruleLogicalId="l2"),
    )
    patch = {
        "block-rdp": add(DENY, first=True),
        "block-ssh": {"logging_enabled": True},
        "allow-branch-erp": move(first=True),
    }
    return await firewall_conflict(raw, patch, other_writer_edit)


async def a_clash() -> Lost:
    """A rule added, and a port set that another writer set too."""
    raw = firewall(fw_rule("allow-web", ruleLogicalId="l1", match={"dport_high": 443}))
    patch = {
        "block-ssh": add(DENY, before="allow-web"),
        "allow-web": {"match": {"dport_high": 8080}},
    }

    def same_port(row: dict[str, Any]) -> None:
        stored_outbound(row)[0]["match"]["dport_high"] = 8443

    return await firewall_conflict(raw, patch, same_port)


async def a_twin_behind_a_new_rule() -> Lost:
    """An edit to the second of two rules that share a name; a rule is stored first."""
    raw = firewall(
        fw_rule("d", ruleLogicalId="l1"),
        fw_rule("d", ruleLogicalId="l2"),
        fw_rule("x", ruleLogicalId="l3"),
    )

    def theirs_first(row: dict[str, Any]) -> None:
        stored_outbound(row).insert(0, fw_rule("theirs", ruleLogicalId="l9"))

    return await firewall_conflict(
        raw, {at(1): {"logging_enabled": True}}, theirs_first
    )


async def a_place_that_is_gone() -> Lost:
    """A rule added after one another writer removed, and an edit."""
    raw = firewall(
        fw_rule("block-ssh", ruleLogicalId="l1"),
        fw_rule("allow-branch-erp", ruleLogicalId="l2"),
    )
    patch = {
        "block-rdp": add(DENY, after="block-ssh"),
        "allow-branch-erp": {"logging_enabled": True},
    }

    def remove_block_ssh(row: dict[str, Any]) -> None:
        del stored_outbound(row)[0]

    return await firewall_conflict(raw, patch, remove_block_ssh)


async def a_reconciled_segment() -> Lost:
    """A rule added in a segment the edge had no entry for."""
    raw = firewall(fw_rule("a", ruleLogicalId="l1"))
    return await firewall_conflict(
        raw,
        {"r": add(DENY)},
        other_writer_edit,
        segment="Guest",
        profile=firewall(guest=True),
    )


async def a_hub_out_of_a_rule() -> Lost:
    """``ports_and_a_hub_lost``, and a state that holds the QoS module as stored."""
    error, _ = await ports_and_a_hub_lost()
    state = await rule_uses_hub()
    state.replace_module(state.module("QOS"), cast("AnyStoredModule", error.module))
    return Lost(error, state)


async def a_network_renamed_and_one_added() -> Lost:
    """``Users`` renamed with a new prefix, and VLAN 40 added; nothing is stored."""
    state = await three_networks()
    networks = state.profile.device_settings.lan.networks
    networks[0].name = "Staff"
    networks[0].cidr_prefix = 25
    networks.append(type(networks[0]).model_validate(net(40, "Lab")))
    error = lan_error(state, as_read(state))
    fresh = await three_networks()
    fresh.replace_module(
        fresh.module("deviceSettings"), cast("AnyStoredModule", error.module)
    )
    return Lost(error, fresh)


async def an_added_segment() -> Lost:
    """Guest added to a profile's QoS with two rules; the VCO stored none of it."""
    state = await global_only()
    pipeline, store = module_store_pipeline(state)
    rules = [template_rule("Box"), template_rule("Video")]
    _ = await pipeline.apply(
        state,
        module="business_policy",
        patch={"segments": {"Guest": add({"rules": rules})}},
    )
    store.on_store.append(guest_stripped)
    with pytest.raises(ModuleWriteError) as caught:
        _ = await pipeline.save(state)
    return Lost(caught.value, state)


async def a_provider_row() -> Lost:
    """A DNS provider set in the Global Segment; the VCO dropped its row."""
    pipeline, client, state = await loaded_profile()
    state = await pipeline.apply(
        state,
        module="deviceSettings",
        segment="Global Segment",
        patch=provider_patch("dns-a"),
    )
    client.store.skipped_objects.add(701)
    with pytest.raises(RefsDroppedError) as caught:
        _ = await pipeline.save(state)
    return Lost(caught.value, state)


async def a_css_provider() -> Lost:
    """A CSS provider set in the Global Segment; another writer's key is stored."""
    pipeline, client, state = await loaded_with_css()
    state = await set_css(pipeline, state, "css-west")
    client.store.other_writer(DS_ID, other_writer_edit)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)
    return Lost(caught.value, state)


RENAMED_LOSSES = [
    a_place_and_a_move,
    a_clash,
    a_twin_behind_a_new_rule,
    a_hub_out_of_a_rule,
    a_network_renamed_and_one_added,
    a_css_provider,
]
"""The writes whose redo patch has a name the VCO's form of it lacks, and whose
lost edits are each redone as it is: an edit of the data, in a segment that is
stored."""
EVERY_LOSS = [
    *RENAMED_LOSSES,
    a_place_that_is_gone,
    a_reconciled_segment,
    an_added_segment,
    a_provider_row,
]


def build_name(build: Callable[[], Awaitable[Lost]]) -> str:
    return getattr(build, "__name__", "")


async def redone(lost: Lost, patch: dict[str, object]) -> list[tuple[object, ...]]:
    """What the state of *lost* plans once *patch* is applied to it."""
    _ = await ConfigPipeline(client=None).apply(
        lost.state, module=lost.error.module_name, patch=patch
    )
    return [
        (module.name, module.changes, module.ref_changes, module.render())
        for module in lost.state.plan().changed
    ]


@pytest.mark.parametrize("build", EVERY_LOSS, ids=build_name)
async def test_the_redo_patch_names_every_field_and_item_as_the_plan_does(
    build: Callable[[], Awaitable[Lost]],
) -> None:
    lost = await build()
    patch = lost.error.redo_patch()
    assert patch
    assert vco_names(json.dumps(patch), forbidden_names()) == [], patch
    assert await redone(lost, patch)


def in_the_vcos_form(error: ModuleWriteError) -> dict[str, object]:
    """The lost data edits of *error* as a patch in the VCO's keys.

    Each body is as the plan holds it, and each item is under the part the
    check found for it in the stored list (``stored_parts``): its key there,
    or its position. A clash is left out, as ``redo_patch()`` leaves it.
    """
    assert error.plan is not None
    assert error.plan.model is not None
    changes = [
        RawChange(
            lost.path,
            MISSING if lost.as_read is MISSING else lost.stored,
            lost.intended,
            place=lost.place,
            moves=lost.moves,
        )
        for lost in error.lost_edits
        if lost.part == "data" and not lost.clash
    ]
    patch, _ = patch_of(changes, error.plan.model, stored_parts=error.stored_parts)
    return patch


@pytest.mark.parametrize("build", RENAMED_LOSSES, ids=build_name)
async def test_the_names_of_the_redo_patch_change_nothing_it_does(
    build: Callable[[], Awaitable[Lost]],
) -> None:
    """Each write is made twice, and one state takes the patch in the VCO's form."""
    named, in_keys = await build(), await build()
    patch, in_vco_form = named.error.redo_patch(), in_the_vcos_form(in_keys.error)
    assert patch != in_vco_form
    planned = await redone(named, patch)
    assert planned
    assert planned == await redone(in_keys, in_vco_form)


async def test_a_provider_is_redone_under_its_attribute() -> None:
    lost = await a_provider_row()
    assert lost.error.plan is not None

    patch = lost.error.redo_patch()

    assert patch == {
        "segments": {
            "Global Segment": {"dns": {"primary_provider": {"service": DNS_A}}}
        }
    }
    # The row the VCO dropped is planned again.
    ((_, _, rows, _),) = await redone(lost, patch)
    assert rows == lost.error.plan.ref_changes


@pytest.mark.parametrize("build", [a_place_that_is_gone, a_clash], ids=build_name)
async def test_what_is_not_redone_reads_as_the_errors_lines_do(
    build: Callable[[], Awaitable[Lost]],
) -> None:
    error = (await build()).error
    (left,) = error.not_redone()
    assert str(left.path) in error.render()
    assert vco_names(str(left.path), forbidden_names()) == []


async def test_the_lost_edits_keep_the_vcos_form_of_what_is_not_redone() -> None:
    error = (await a_clash()).error
    (left,) = error.not_redone()
    (clash,) = [lost for lost in error.lost_edits if lost.clash]
    assert (str(clash.path), str(left.path)) == (
        f"{OUTBOUND}[allow-web].match.dport_high",
        f"{OUTBOUND}[allow-web].match.destination_port_high",
    )
    assert (clash.as_read, clash.intended, clash.stored) == (443, 8080, 8443)


def test_a_secret_in_the_redo_patch_is_complete() -> None:
    """A mask in a patch would be written to the VCO as the secret."""
    module, plan = ntp_and_a_vlan()
    error = failed(module, plan, raw_copy(module))
    assert SECRET not in error.render()

    server = cast("dict[str, Any]", error.redo_patch()["ntp_server"])

    assert server["keys"] == [{"key_number": 1, "key": SECRET}]


async def test_the_redo_patch_names_a_stored_item_as_the_stored_list_shows_it() -> None:
    """A network is under its name as stored: not its VLAN ID, nor its new name."""
    error = (await a_network_renamed_and_one_added()).error
    assert [str(lost.path) for lost in error.lost_edits] == [
        "lan.networks[10].name",
        "lan.networks[10].cidrPrefix",
        "lan.networks[40]",
    ]

    lan = cast("dict[str, dict[str, dict[str, Any]]]", error.redo_patch()["lan"])
    ops = lan["networks"]
    assert ops["Users"] == {"name": "Staff", "cidr_prefix": 25}
    # An added item has no stored item to be named by: it is under the plan's
    # key for it, and the item it goes after is under its name.
    assert sorted(ops) == ["40", "Users"]
    lab = ops["40"]
    assert (lab["_op"], lab["name"], lab["cidr_ip"], lab["_position"]) == (
        "add",
        "Lab",
        "10.40.0.0",
        {"after": "Guests"},
    )
