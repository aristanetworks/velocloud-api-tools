"""A write error names fields, items and the module as the plan's text does.

The lines of ``ModuleWriteError.render()`` show each path as the plan of the
write shows it; the lists of the error (``lost_edits``, ``applied``,
``server_changes``) keep the paths the check made, in the VCO's keys.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, cast

import pytest
from hub_state import OUT_OF_THE_RULE, edit, guest_backhaul_state, rule_uses_hub
from pydantic import MISSING
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.module_check import ModuleWrite, check_module_write
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.config.errors import SaveStepError
from pyvelo.config.pipeline import (
    ConfigPipeline,
    _how_it_differs,  # pyright: ignore[reportPrivateUsage]  # the refusal's text is built here
)
from pyvelo.core.rebase import Key, Path
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import as_stored
from test_shown_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    LAN,
    SECRET,
    device_settings,
    lan_plan,
    lan_state,
    net,
    rule_with_id,
)

if TYPE_CHECKING:
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase
    from pyvelo.config.state import ConfigState

HUB = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
ROW = '"deviceSettings:backHaulEdge"[aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa,502]'
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


async def test_a_lost_edit_reads_as_the_plan_s_line_for_it() -> None:
    """Two ports and a hub of a rule's backhaul list; the VCO stored one port."""
    state = await rule_uses_hub()
    await edit(state, "QOS", PORTS)
    await edit(state, "QOS", OUT_OF_THE_RULE)
    (plan,) = [m for m in state.plan().modules if m.name == "QOS"]
    module = state.module("QOS")
    stored = raw_copy(module)
    (guest,) = [s for s in stored["segments"] if s["segment"]["name"] == "Guest"]
    guest["rules"][0]["match"].update({"dport_high": 443, "serverAdded": "x"})

    error = failed(module, plan, stored)

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
    assert row == f'  refs {ROW}: read "***", meant (nothing), stored "***"'
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
    # The lists keep the paths of the check, and the redo patch is as it was.
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
                                    "backhaul_edges": {HUB: {"_op": "delete"}}
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
