"""The text of a plan names what it changes as pyvelo shows it.

A module by its python name, a field by its attribute and an item by the text
that names it: in the data as edited where the place is still there, in the
data as read where the edit removed it. An object after ``+`` has its keys by
attribute name. What the plan holds (its ``name``, its ``changes``) stays in
the VCO's names: the save reads those.
"""

import copy
from typing import Any

from hub_helpers import HUB_A, HUB_B
from hub_state import (
    FROM_BOTH_LISTS,
    OUT_OF_THE_RULE,
    edit,
    guest_backhaul_state,
    hub_backhaul_rule,
    rule_uses_hub,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.config import ConfigPipeline, add
from pyvelo.config.state import ConfigState
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import make_module_json
from shown_paths import reached

ROUTE_HUBS = "action.edge_to_edge_route_action.route_cfg.backhaul_edges"
"""The backhaul hubs of a rule's Edge to Edge route, below the rule."""
RENDERED = f"""\
profile "P", configuration 100
business_policy: update
  ~ segments[Guest].rules[Web].match.destination_port_low: -1 -> 443
  - segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-A]
  - refs deviceSettings:backHaulEdge (Guest): aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
firewall: update
  + firewall_logging_enabled: true
steps:
  disable hub for Hub-A on segment Guest
device_settings: update
  ~ segments[Guest].vpn.hub_edges.enabled: true -> false
  - segments[Guest].vpn.hub_edges.vpn_hubs[Hub-A]
  - segments[Guest].vpn.backhaul_edges[Hub-A]
  refs: sent as read"""
PORT = {"segments": {"Guest": {"rules": {"Web": {"match": {"dport_low": 443}}}}}}
RENAMED = {"segments": {"Guest": {"rules": {"Web": {"name": "Web2"}}}}}
SECRET = "NTP-key-made-up"


async def edited() -> ConfigState:
    state = await rule_uses_hub()
    await edit(state, "deviceSettings", FROM_BOTH_LISTS)
    await edit(state, "QOS", OUT_OF_THE_RULE)
    await edit(state, "firewall", {"firewall_logging_enabled": True})
    await edit(state, "QOS", PORT)
    return state


def module_lines(state: ConfigState, name: str) -> list[str]:
    """The lines of the plan of the module the VCO calls *name*, without its heading."""
    (module,) = [m for m in state.plan().modules if m.name == name]
    return module.render().splitlines()[1:]


def desc(segment_id: int, name: str) -> dict[str, object]:
    return {
        "segmentId": segment_id,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{segment_id:012d}",
    }


def device_settings(raw: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    """A device settings module read with *raw* as its data."""
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(raw), configuration_id=10)
    )


LAN: dict[str, Any] = {
    "lan": {
        "networks": [],
        "interfaces": [
            {"name": "GE1", "type": "wired"},
            {"name": "GE2", "type": "wired"},
            {"name": "GE3", "type": "wired"},
        ],
    },
    "segments": [{"segment": desc(0, "Global Segment")}],
}


async def test_the_plan_names_modules_fields_and_items() -> None:
    assert (await edited()).plan().render() == RENDERED


async def test_the_plan_keeps_the_names_the_save_reads() -> None:
    plan = (await edited()).plan()
    assert [m.name for m in plan.modules] == ["QOS", "firewall", "deviceSettings"]
    (port, hub) = plan.modules[0].changes
    assert port.path == "segments[Guest].rules[Web].match.dport_low"
    assert hub.path.parts[-2:] == ("backhaulEdges", Key(HUB_A.logical_id))


async def test_an_added_object_is_written_in_names() -> None:
    """A hub added to Guest's backhaul list: its ``+`` line."""
    state = await rule_uses_hub()
    hub = {"+": add({"name": HUB_B.name})}
    vpn = {"backHaulEdges": hub, "edgeToEdgeHub": {"vpnHubs": hub}}
    await edit(state, "deviceSettings", {"segments": {"Guest": {"vpn": vpn}}})
    (line,) = [
        line
        for line in module_lines(state, "deviceSettings")
        if ".backhaul_edges[" in line
    ]
    path, _, value = line.partition(": ")
    assert path == "  + segments[Guest].vpn.backhaul_edges[Hub-B] (after Hub-A)"
    assert value.startswith(f'{{"logical_id": "{HUB_B.logical_id}", "name": "Hub-B"')
    assert '"logicalId"' not in value


async def test_a_renamed_item_is_named_as_edited() -> None:
    state = await rule_uses_hub()
    await edit(state, "QOS", PORT)
    await edit(state, "QOS", RENAMED)
    assert module_lines(state, "QOS")[:2] == [
        '  ~ segments[Guest].rules[Web2].name: "Web" -> "Web2"',
        "  ~ segments[Guest].rules[Web2].match.destination_port_low: -1 -> 443",
    ]


async def test_the_path_of_a_plan_line_is_a_path_apply_takes() -> None:
    """Of a rule renamed since it was read: the line names it as the state has it."""
    state = await rule_uses_hub()
    await edit(state, "QOS", PORT)
    await edit(state, "QOS", RENAMED)
    path, _, values = module_lines(state, "QOS")[1].removeprefix("  ~ ").partition(": ")
    assert values == "-1 -> 443"
    assert reached(state.profile.qos, Path.parse(path)) == 443
    apply = ConfigPipeline(client=None).apply
    _ = await apply(state, module="business_policy", path=path, patch=8443)
    assert state.profile.qos.segments[1].rules[0].match.destination_port_low == 8443
    assert f"  ~ {path}: -1 -> 8443" in module_lines(state, "QOS")


async def test_an_item_removed_from_a_renamed_item_is_named_below_the_new_name() -> (
    None
):
    """The rule is there after the edit, under its new name; the hub is as read."""
    state = await rule_uses_hub()
    await edit(state, "QOS", OUT_OF_THE_RULE)
    await edit(state, "QOS", RENAMED)
    assert f"  - segments[Guest].rules[Web2].{ROUTE_HUBS}[Hub-A]" in module_lines(
        state, "QOS"
    )


async def test_two_items_that_swap_names_are_each_named_as_edited() -> None:
    """A change is shown at the item it is a change of.

    Not at the item that now has the name it was read under.
    """
    state = await guest_backhaul_state(
        hub_backhaul_rule("One", HUB_A), hub_backhaul_rule("Two", HUB_A)
    )
    one, two = state.profile.qos.segments[1].rules
    one.name, two.name = "Two", "One"
    one.match.destination_port_low = 443
    assert module_lines(state, "QOS")[:3] == [
        '  ~ segments[Guest].rules[Two].name: "One" -> "Two"',
        "  ~ segments[Guest].rules[Two].match.destination_port_low: -1 -> 443",
        '  ~ segments[Guest].rules[One].name: "Two" -> "One"',
    ]


async def test_a_plan_kept_names_items_as_they_were_when_it_was_made() -> None:
    state = await rule_uses_hub()
    await edit(state, "QOS", PORT)
    plan = state.plan()
    text = plan.render()
    assert "  ~ segments[Guest].rules[Web].match.destination_port_low: -1 -> 443" in (
        text.splitlines()
    )
    await edit(state, "QOS", RENAMED)
    assert plan.render() == text
    planned = plan.modules[1].planned
    assert planned is not None
    assert planned is not state.profile.qos


async def test_an_order_line_names_the_items_that_move() -> None:
    state = await guest_backhaul_state()
    hubs = state.profile.device_settings.segments[1].vpn.backhaul_edges
    hubs.reverse()
    assert module_lines(state, "deviceSettings")[0] == (
        "  ~ segments[Guest].vpn.backhaul_edges: order: Hub-B first"
    )


def test_a_rename_where_the_name_is_the_key_is_a_removal_and_an_addition() -> None:
    """A LAN interface is keyed by its name and has no other key.

    One renamed is one removed, named as read, and one added, named as edited.
    """
    module = device_settings(LAN)
    module.data.lan.interfaces[1].name = "LAN2"
    plan = plan_module(module)
    assert [str(change.path) for change in plan.changes] == [
        "lan.interfaces[GE2]",
        "lan.interfaces[LAN2]",
    ]
    removed, added = plan.render().splitlines()[1:3]
    assert removed == "  - lan.interfaces[GE2]"
    assert added.startswith('  + lan.interfaces[LAN2] (after GE1): {"cwp": false')


def test_a_secret_in_an_added_object_is_hidden_under_its_attribute_name() -> None:
    module = device_settings(LAN)
    keys = [{"keyNumber": 1, "key": SECRET}]
    _ = module.data.merge_patch(
        {"ntp_server": {"enabled": True, "authentication": "MD5", "keys": keys}}
    )
    plan = plan_module(module)
    (change,) = plan.changes
    assert change.after == {  # the change keeps the value, in the VCO's keys
        "override": True,
        "enabled": True,
        "authentication": "MD5",
        "keys": keys,
    }
    text = plan.render()
    assert SECRET not in text
    assert (
        '  + ntp_server: {"authentication": "MD5", "enabled": true, '
        '"keys": [{"key": "***", "key_number":…'  # cut to 80 characters
    ) in text.splitlines()


def test_a_plan_made_from_a_mapping_names_fields_by_the_class() -> None:
    """It has no model to find an item in, and the class of the module's data."""
    raw = {**LAN, "ntpServer": {"enabled": False, "authentication": "NONE"}}
    module = device_settings(raw)
    data = {**LAN, "ntpServer": {"enabled": True, "authentication": "NONE"}}
    plan = plan_module(module, data)
    assert plan.planned is None
    assert plan.as_read is None
    assert plan.render().splitlines()[1] == "  ~ ntp_server.enabled: false -> true"
    path = Path(("lan", "interfaces", Key("GE1"), "vlanIds"))
    assert plan.named(path) == "lan.interfaces[GE1].vlan_ids"
    assert plan.named(path, removed=True) == "lan.interfaces[GE1].vlan_ids"


def test_named_shows_a_path_of_the_plan() -> None:
    module = device_settings(LAN)
    module.data.lan.interfaces[0].vlan_ids = [10]
    plan = plan_module(module)
    path = Path(("lan", "interfaces", Key("GE1"), "vlanIds"))
    assert plan.named(path) == "lan.interfaces[GE1].vlan_ids"
    assert plan.named(path, removed=True) == "lan.interfaces[GE1].vlan_ids"
