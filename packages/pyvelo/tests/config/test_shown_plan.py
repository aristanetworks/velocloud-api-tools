"""The text of a plan names what it changes as pyvelo shows it.

A module by its python name, a field by its attribute and an item by the text
that names it: in the data as edited where the place is still there, in the
data as read where the edit removed it. An object after ``+`` has its keys by
attribute name. What the plan holds (its ``name``, its ``changes``) stays in
the VCO's names: the save reads those.
"""

import copy
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from hub_helpers import HUB_A, HUB_B
from hub_state import (
    FROM_BOTH_LISTS,
    OUT_OF_THE_RULE,
    edit,
    guest_backhaul_state,
    hub_backhaul_rule,
    rule_uses_hub,
)
from profile_state import profile_state
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.config import ConfigPipeline, add, move
from pyvelo.config.state import ConfigState
from pyvelo.core import delete
from pyvelo.core.names import ChangeNames
from pyvelo.core.rebase import (
    ItemMove,
    ItemPart,
    ItemPlace,
    Key,
    KeyedList,
    Path,
    item_text,
    node_child,
    pair_items,
)
from pyvelo.testing import make_module_json
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from shown_paths import reached
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    SEGMENT as FW_SEGMENT,
)
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    firewall,
    fw_rule,
    make_state,
)

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


HUB = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
ENTRY = "segments[Guest].rules[Web].action.edge_to_edge_route_action.route_cfg"


async def test_field_changes_are_in_the_names_of_the_plan_text() -> None:
    changes = (await edited()).get_field_changes()
    assert [(c.module, str(c.path)) for c in changes] == [
        ("business_policy", "segments[Guest].rules[Web].match.destination_port_low"),
        ("business_policy", f"{ENTRY}.backhaul_edges[Hub-A]"),
        ("firewall", "firewall_logging_enabled"),
        ("device_settings", "segments[Guest].vpn.hub_edges.enabled"),
        ("device_settings", "segments[Guest].vpn.hub_edges.vpn_hubs[Hub-A]"),
        ("device_settings", "segments[Guest].vpn.backhaul_edges[Hub-A]"),
    ]
    removed = changes[-1].before
    assert isinstance(removed, dict)
    assert removed["logical_id"] == HUB
    assert "logicalId" not in removed


async def test_the_path_of_every_field_change_is_in_the_plan_text() -> None:
    state = await edited()
    lines = state.plan().render().splitlines()
    for change in state.get_field_changes():
        heads = [f"  {sign} {change.path}" for sign in "+-~"]
        # The path ends where the line does, at its ": " or at a place's " (".
        assert any(
            line == head or line.startswith((f"{head}: ", f"{head} ("))
            for line in lines
            for head in heads
        )


async def test_either_module_name_filters() -> None:
    state = await edited()
    assert state.get_field_changes("business_policy") == state.get_field_changes("QOS")
    assert len(state.get_field_changes("business_policy")) == 2


async def test_a_renamed_item_is_shown_by_its_new_name() -> None:
    state = await rule_uses_hub()
    await edit(
        state, "QOS", {"segments": {"Guest": {"rules": {"Web": {"name": "Web2"}}}}}
    )
    (change,) = state.get_field_changes("business_policy")
    assert (str(change.path), change.before, change.after) == (
        "segments[Guest].rules[Web2].name",
        "Web",
        "Web2",
    )
    assert reached(state.profile.qos, change.path) == "Web2"


async def test_an_order_entry_names_its_items_as_shown() -> None:
    state = await guest_backhaul_state()
    hubs = state.profile.device_settings.segments[1].vpn.backhaul_edges
    hubs.reverse()
    (change,) = state.get_field_changes("device_settings")
    assert (str(change.path), change.before, change.after, change.moves) == (
        "segments[Guest].vpn.backhaul_edges",
        ["Hub-A", "Hub-B"],
        ["Hub-B", "Hub-A"],
        (ItemMove(Key("Hub-B"), ItemPlace()),),
    )


# ---------------------------------------------------------------------------
# An item is paired between the data as read and as edited as the save pairs it
# ---------------------------------------------------------------------------


def net(vlan_id: int, name: str, host: int = 0) -> dict[str, Any]:
    """A LAN network: keyed by its VLAN ID, shown by its name."""
    return {
        "vlanId": vlan_id,
        "name": name,
        "segmentId": 0,
        "cidrIp": f"10.{vlan_id}.{host}.0",
        "cidrPrefix": 24,
    }


async def lan_state(*networks: dict[str, Any]) -> ConfigState:
    """A profile whose device settings have the LAN *networks*."""
    return await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        networks=list(networks),
    )


def lan_plan(state: ConfigState) -> ModulePlan:
    (module,) = [m for m in state.plan().modules if m.name == "deviceSettings"]
    return module


def lan_lines(state: ConfigState) -> list[str]:
    """The lines of the device settings plan about ``lan.networks``, each cut at ``:``.

    An added item's line is cut before its value.
    """
    lines = lan_plan(state).render().splitlines()
    return [
        line.split(": {", maxsplit=1)[0] for line in lines if " lan.networks" in line
    ]


async def deleted_then_added() -> ConfigState:
    """``Old``, VLAN 10, is deleted, and a network ``New`` is added as VLAN 10."""
    state = await lan_state(net(1, "first"), net(10, "Old"))
    await edit(state, "deviceSettings", {"lan": {"networks": {"Old": delete()}}})
    new = add({"name": "New", "segment_id": 0})
    await edit(state, "deviceSettings", {"lan": {"networks": {"10": new}}})
    return state


async def removed_before_two_of_one_key() -> ConfigState:
    """``X`` is removed from before ``a`` and ``b``, both VLAN 1; ``D`` is added."""
    state = await lan_state(net(9, "X"), net(1, "a", 1), net(1, "b", 2))
    del state.profile.device_settings.lan.networks[0]
    new = add({"name": "D", "segment_id": 0})
    await edit(state, "deviceSettings", {"lan": {"networks": {"5": new}}})
    return state


async def reordered_with_one_key_twice() -> ConfigState:
    """``A`` and ``B`` are both VLAN 1; ``A`` goes last."""
    state = await lan_state(net(1, "A", 1), net(2, "C"), net(1, "B", 2))
    networks = state.profile.device_settings.lan.networks
    networks[:] = [networks[1], networks[2], networks[0]]
    return state


async def test_an_item_added_under_the_key_of_one_removed_is_not_that_item() -> None:
    """The ``-`` line names the item removed and the ``+`` line the item added.

    Both changes are at ``lan.networks[10]``: the key is all they share.
    """
    state = await deleted_then_added()
    plan = lan_plan(state)
    assert [str(change.path) for change in plan.changes] == [
        "lan.networks[10]",
        "lan.networks[10]",
    ]
    assert lan_lines(state) == [
        "  - lan.networks[Old]",
        "  + lan.networks[New] (after first)",
    ]
    removed, added = state.get_field_changes("device_settings")
    assert (str(removed.path), str(added.path)) == (
        "lan.networks[Old]",
        "lan.networks[New]",
    )
    assert isinstance(removed.before, dict)
    assert isinstance(added.after, dict)
    assert (removed.before["name"], added.after["name"]) == ("Old", "New")
    assert added.place == ItemPlace(Key("first"))


async def test_a_model_put_where_one_of_its_key_was_is_that_item_changed() -> None:
    """Only an ``add`` makes a new item: a model with the key is paired by it."""
    state = await lan_state(net(1, "first"), net(10, "Old"))
    networks = state.profile.device_settings.lan.networks
    networks[1] = type(networks[1]).model_validate(net(10, "New"))
    assert lan_lines(state) == ['  ~ lan.networks[New].name: "Old" -> "New"']


async def test_a_place_after_an_item_named_by_position_names_that_item() -> None:
    """``b`` is ``#2`` as read and ``#1`` as edited, where ``#2`` is the added item."""
    state = await removed_before_two_of_one_key()
    removal, addition = lan_plan(state).changes
    assert (str(removal.path), str(addition.path)) == (
        "lan.networks[9]",
        "lan.networks[5]",
    )
    assert addition.place == ItemPlace(2)
    assert lan_lines(state) == [
        "  - lan.networks[X]",
        "  + lan.networks[D] (after b)",
    ]
    assert state.get_field_changes("device_settings")[1].place == ItemPlace(Key("b"))


async def test_a_move_of_an_item_named_by_position_names_that_item() -> None:
    """``A`` is ``#0`` as read and ``B`` ``#2``; as edited they are ``#2``, ``#1``."""
    state = await reordered_with_one_key_twice()
    (order,) = lan_plan(state).changes
    assert order.moves == (ItemMove(0, ItemPlace(2)),)
    assert lan_lines(state) == ["  ~ lan.networks: order: A after B"]
    (change,) = state.get_field_changes("device_settings")
    assert change.moves == (ItemMove(Key("A"), ItemPlace(Key("B"))),)
    assert (change.before, change.after) == (["A", "C", "B"], ["C", "B", "A"])


async def test_an_item_only_a_position_names_is_shown_at_its_position_as_edited() -> (
    None
):
    """Two networks of one VLAN and one name, the first as read moved last.

    The move is ``#0 after #2`` in positions as read; the item is at ``#2`` as
    edited, after the one at ``#1``.
    """
    state = await lan_state(net(1, "n", 1), net(2, "C"), net(1, "n", 2))
    networks = state.profile.device_settings.lan.networks
    networks[:] = [networks[1], networks[2], networks[0]]
    (order,) = lan_plan(state).changes
    assert order.moves == (ItemMove(0, ItemPlace(2)),)
    assert lan_lines(state) == ["  ~ lan.networks: order: #2 after #1"]


async def two_added_of_one_key(first: str, second: str) -> ConfigState:
    """Two networks of VLAN 7 put before the three as read: no key names either."""
    state = await lan_state(net(9, "X"), net(1, "a"), net(2, "b"))
    lan = state.profile.device_settings.lan
    rows = [net(7, first, 1), net(7, second, 2)]
    lan.networks[:0] = [type(lan.networks[0]).model_validate(row) for row in rows]
    return state


async def test_an_added_item_named_by_position_is_the_item_there_as_edited() -> None:
    """``#0`` and ``#1`` are the added items, not ``X`` and ``a``, there as read."""
    state = await two_added_of_one_key("p", "q")
    first, second = lan_plan(state).changes
    assert (str(first.path), str(second.path)) == (
        "lan.networks[#0]",
        "lan.networks[#1]",
    )
    assert second.place == ItemPlace(0)
    assert lan_lines(state) == [
        "  + lan.networks[p] (first)",
        "  + lan.networks[q] (after p)",
    ]


async def test_an_added_item_nothing_names_keeps_its_position_as_edited() -> None:
    state = await two_added_of_one_key("p", "p")
    assert lan_lines(state) == [
        "  + lan.networks[#0] (first)",
        "  + lan.networks[#1] (after #0)",
    ]


def rule_without_id(name: str, port: int) -> dict[str, Any]:
    """A QoS rule as read without a ``ruleLogicalId``: each parse makes it one."""
    rule = hub_backhaul_rule(name, HUB_A)
    del rule["ruleLogicalId"]
    rule["match"]["dport_low"] = port
    return rule


async def test_items_read_without_an_id_are_paired_as_the_documents_have_them() -> None:
    """Two rules of one name and no ID as read, and a rule added at each end.

    The ID each parse makes for them is in no document, so the save pairs
    each with itself, and places the last rule after ``#2`` as read. That
    rule is ``#3`` as edited, where ``#2`` is the rule ``Other``.
    """
    state = await guest_backhaul_state(
        rule_without_id("Web", 80),
        rule_without_id("Other", 81),
        rule_without_id("Web", 82),
    )
    body = {k: v for k, v in rule_without_id("New", 83).items() if k != "name"}
    for name, place in (("New1", {"first": True}), ("New2", {"last": True})):
        patch = {"segments": {"Guest": {"rules": {name: add(body, **place)}}}}
        await edit(state, "QOS", patch)
    (module,) = [m for m in state.plan().modules if m.name == "QOS"]
    _, last = module.changes
    assert last.place == ItemPlace(2)
    second_web = state.profile.qos.segments[1].rules[3]
    assert (second_web.name, second_web.match.destination_port_low) == ("Web", 82)
    # No name and no key is that rule's alone: it is shown by its alt key.
    assert module.shown_change(last).place == ItemPlace(
        Key(str(second_web.rule_logical_id))
    )


@pytest.mark.parametrize(
    "made",
    [
        deleted_then_added,
        removed_before_two_of_one_key,
        reordered_with_one_key_twice,
    ],
)
async def test_the_plan_keeps_the_pairing_its_changes_were_made_by(
    made: Callable[[], Awaitable[ConfigState]],
) -> None:
    """What names a plan's items is the pairing of the documents the save compared."""
    state = await made()
    plan = lan_plan(state)
    read, planned = plan.as_read, plan.planned
    assert isinstance(read, DeviceSettingsData)
    assert isinstance(planned, DeviceSettingsData)
    node = node_child(type(read.lan), "networks")
    assert isinstance(node, KeyedList)
    profile = state.profile_config
    assert profile is not None
    as_read: Any = profile.device_settings.raw_data
    pairing = pair_items(as_read["lan"]["networks"], plan.data["lan"]["networks"], node)
    assert pairing is not None
    assert plan.paired_lists[Path.parse("lan.networks")].pairs == pairing.pairs
    assert pairing.pairs


def rule_with_id(name: str, port: int) -> dict[str, Any]:
    """A QoS rule as read with a ``ruleLogicalId``."""
    rule = hub_backhaul_rule(name, HUB_A)
    rule["match"]["dport_low"] = port
    return rule


async def test_a_second_item_of_a_name_put_first_names_each_item_itself() -> None:
    """``Other`` and ``Web`` read without an ID, and a second ``Web`` put first.

    It is put in the list through the model, so the save pairs neither
    ``Web``: the one as read is removed, and the two as edited are added, at
    ``#0`` and ``#2``. The removal is named in the data as read, where the
    name is that rule's alone; each added rule is the one at its position as
    edited, and no name or key is its alone, so it is shown by its alt key.
    """
    state = await guest_backhaul_state(
        rule_without_id("Other", 81),
        rule_without_id("Web", 80),
        rule_with_id("X", 82),
        rule_with_id("Mail", 83),
    )
    rules = state.profile.qos.segments[1].rules
    rules.insert(0, type(rules[0]).model_validate(rule_with_id("Web", 99)))
    (module,) = [m for m in state.plan().modules if m.name == "QOS"]
    removal, first, second = module.changes
    assert (str(removal.path), str(first.path), str(second.path)) == (
        "segments[Guest].rules[Web]",
        "segments[Guest].rules[#0]",
        "segments[Guest].rules[#2]",
    )
    assert second.place == ItemPlace(Key("Other"))
    in_guest = Path(("segments", Key("Guest"), "rules"))
    new_web, _, old_web = (Key(str(rule.rule_logical_id)) for rule in rules[:3])
    assert [line.split(": {", maxsplit=1)[0] for line in module.render().splitlines()][
        1:4
    ] == [
        "  - segments[Guest].rules[Web]",
        f"  + {in_guest.child(new_web)} (first)",
        f"  + {in_guest.child(old_web)} (after Other)",
    ]
    changes = state.get_field_changes("business_policy")
    assert [(str(change.path), change.place) for change in changes] == [
        ("segments[Guest].rules[Web]", None),
        (str(in_guest.child(new_web)), ItemPlace()),
        (str(in_guest.child(old_web)), ItemPlace(Key("Other"))),
    ]
    assert reached(state.profile.qos, changes[2].path) is rules[2]
    assert module.paired_lists[in_guest].pairs == ((0, 1), (2, 3), (3, 4))


async def test_items_of_one_key_made_through_the_model_are_named_where_they_are() -> (
    None
):
    """``a`` patched below a key absent as read, ``c`` moved first, ``b`` appended.

    ``b`` has the VLAN of ``a``, so the save pairs only ``c``: ``a`` is removed
    and added again at ``#1``, and ``b`` is added at ``#2``, after ``#1``.
    Those are positions as edited, where ``#1`` is ``a`` and not ``c``.
    """
    state = await lan_state(net(2, "a"), net(3, "c"))
    lease = {"lan": {"networks": {"a": {"dhcp": {"lease_time_seconds": 600}}}}}
    await edit(state, "deviceSettings", lease)
    networks = state.profile.device_settings.lan.networks
    networks[:] = [networks[1], networks[0]]
    networks.append(type(networks[0]).model_validate(net(2, "b", 5)))
    removal, first, second = lan_plan(state).changes
    assert (str(removal.path), str(first.path), str(second.path)) == (
        "lan.networks[2]",
        "lan.networks[#1]",
        "lan.networks[#2]",
    )
    assert (first.place, second.place) == (ItemPlace(Key(3)), ItemPlace(1))
    assert lan_lines(state) == [
        "  - lan.networks[a]",
        "  + lan.networks[a] (after c)",
        "  + lan.networks[b] (after a)",
    ]
    changes = state.get_field_changes("device_settings")
    assert [(str(change.path), change.place) for change in changes] == [
        ("lan.networks[a]", None),
        ("lan.networks[a]", ItemPlace(Key("c"))),
        ("lan.networks[b]", ItemPlace(Key("a"))),
    ]


async def test_an_item_replaced_by_a_model_of_its_values_is_named_as_it_is_paired() -> (
    None
):
    """``b`` is replaced by a model without the ``cost`` it was read with, and moved.

    The save pairs it with itself, by the values written, and moves ``#1`` as
    read first: that is ``b``, whose VLAN the second ``n`` has too.
    """
    state = await lan_state(net(2, "n", 1), {**net(1, "b"), "cost": 10}, net(1, "n", 2))
    networks = state.profile.device_settings.lan.networks
    row = networks[1].model_dump(mode="json", by_alias=True, exclude_unset=True)
    del row["cost"]
    networks[1] = type(networks[1]).model_validate(row)
    networks.insert(0, networks.pop(1))
    (order,) = lan_plan(state).changes
    assert order.moves == (ItemMove(1, ItemPlace()),)
    assert lan_lines(state) == ["  ~ lan.networks: order: b first"]
    (change,) = state.get_field_changes("device_settings")
    assert change.moves == (ItemMove(Key("b"), ItemPlace()),)
    # ``2`` is the first ``n``, by its VLAN; nothing names the second alone.
    assert (change.before, change.after) == (["2", "b", "#2"], ["b", "2", "#2"])


@pytest.mark.parametrize("name", ["lb", "#2", "#0", "c]"])
async def test_an_added_item_is_never_found_by_reading_its_name_as_a_selector(
    name: str,
) -> None:
    """A rule added first whose name reads, as a selector, as another rule.

    ``lb`` is the logical ID of ``b``, and ``#2`` the position of ``c`` as
    read. The plan text and the field changes name the added rule, in its
    line and where ``d`` is moved after it.
    """
    rules = [fw_rule(n, ruleLogicalId=f"l{n}") for n in "abcd"]
    state = make_state({"firewall": firewall(*rules)}, {"firewall": firewall()})
    body = {"name": name, "action": {"allow_or_deny": "deny"}, "match": {}}
    for patch in ({"+new": add(body, first=True)}, {"d": move(after="#0")}):
        _ = await ConfigPipeline(None).apply(
            state, module="firewall", segment=FW_SEGMENT, path="outbound", patch=patch
        )
    held = state.edge.firewall.segment(FW_SEGMENT).outbound
    assert [rule.name for rule in held] == [name, "d", "a", "b", "c"]
    outbound = Path(("segments", Key(FW_SEGMENT), "outbound"))
    text = item_text(Key(name))
    lines = [
        line.split(": {", maxsplit=1)[0]
        for line in state.plan().render().splitlines()
        if " segments[" in line
    ]
    assert lines == [
        f"  + {outbound.child(Key(name))} (first)",
        f"  ~ {outbound}: order: d after {text}",
    ]
    added, order = state.get_field_changes("firewall")
    assert (str(added.path), added.place) == (
        str(outbound.child(Key(name))),
        ItemPlace(),
    )
    assert reached(state.edge.firewall, added.path) is held[0]
    assert str(order.path) == str(outbound)
    assert order.moves == (ItemMove(Key("d"), ItemPlace(Key(name))),)
    assert (order.before, order.after) == (
        ["a", "b", "c", "d"],
        [text, "d", "a", "b", "c"],
    )


async def test_an_order_line_names_only_the_moves_it_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five moves are named and the rest counted; a field change has them all."""
    state = await lan_state(*(net(n, f"n{n}") for n in range(1, 9)))
    state.profile.device_settings.lan.networks.reverse()
    plan = lan_plan(state)
    (order,) = plan.changes
    assert order.moves is not None
    assert len(order.moves) == 7
    items = ChangeNames.items
    named: list[ItemPart] = []

    def counted(self: ChangeNames, path: Path) -> Callable[[ItemPart], ItemPart] | None:
        name = items(self, path)
        assert name is not None

        def count(item: ItemPart) -> ItemPart:
            named.append(item)
            return name(item)

        return count

    monkeypatch.setattr(ChangeNames, "items", counted)
    assert plan.render().splitlines()[1] == (
        "  ~ lan.networks: order: n8 first; n7 after n8; n6 after n7; "
        "n5 after n6; n4 after n5; and 2 more"
    )
    # Five items and the four they are placed after.
    assert len(named) == 9
    (change,) = state.get_field_changes("device_settings")
    assert change.moves is not None
    assert [str(move) for move in change.moves][-2:] == ["n3 after n4", "n2 after n3"]


def work(call: Callable[[], object]) -> int:
    """The Python functions *call* runs, each call counted: its work, not its time."""
    calls = 0

    def count(_frame: object, event: str, _arg: object) -> None:
        nonlocal calls
        if event == "call":
            calls += 1

    sys.setprofile(count)
    try:
        _ = call()
    finally:
        sys.setprofile(None)
    return calls


async def one_rule_moved_first(count: int) -> ConfigState:
    """A firewall of *count* outbound rules, the last of them moved to first."""
    rules = [fw_rule(f"r{i}", ruleLogicalId=f"l{i}") for i in range(count)]
    state = make_state({"firewall": firewall(*rules)}, {"firewall": firewall()})
    _ = await ConfigPipeline(None).apply(
        state,
        module="firewall",
        segment=FW_SEGMENT,
        path="outbound",
        patch={f"r{count - 1}": move(first=True)},
    )
    return state


async def test_an_order_entry_is_named_in_work_that_grows_with_the_list() -> None:
    """Each item of the list is named once, not looked up in the list once each.

    Four times the rules is at most about four times the work, for the plan
    text and for the field changes, both of which plan the module again.
    """
    small, large = await one_rule_moved_first(40), await one_rule_moved_first(160)
    for state in (small, large):  # the caches a first plan fills
        _ = state.plan().render()
        _ = state.get_field_changes()
    assert work(lambda: large.plan().render()) <= 4.5 * work(
        lambda: small.plan().render()
    )
    assert work(large.get_field_changes) <= 4.5 * work(small.get_field_changes)
    (change,) = large.get_field_changes()
    assert change.after == ["r159", *(f"r{i}" for i in range(159))]
    assert change.moves == (ItemMove(Key("r159"), ItemPlace()),)
