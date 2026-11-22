"""Write-back on the real configuration models, through ``ConfigPipeline``.

Each test builds an edge state from module rows in wire form, edits it through
``apply`` and saves it to a ``ModuleStore`` (``MockVcoClient.for_state``),
whose hooks store values as the VCO does. It then checks the state, the
save's ``server_changes`` (or the error's lists) and the stored row. The edge
configuration is 10 and its first module 11; the profile is 20.
"""

import uuid
from collections.abc import Iterable
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import ConfigConflictError
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo_intent import add, move
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    SEGMENT,
    U1,
    desc,
    firewall,
    fw_rule,
    link,
    logical_id,
    make_state,
    qos,
    qos_rule,
    qos_rules,
    wan_links,
)

FIREWALL = ConfigurationModuleName.FIREWALL
MODULE = 11
"""The ID of the edge module each test edits: the only one, or the first."""
ISSUED = str(uuid.UUID(int=901))
"""The ``ruleLogicalId`` the VCO issues to a new firewall rule."""
REVISED = str(uuid.UUID(int=902))
"""The ``ruleLogicalId`` the VCO gives a firewall rule whose content changed."""
U9 = uuid.UUID(int=9)
"""The ``internalId`` the VCO issues to a new WAN link."""
OUTBOUND = 'segments["Global Segment"].outbound'


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    """The path of each entry, sorted: the order within one tuple is not asserted."""
    return sorted(str(x.path) for x in xs)


def assert_state_is_the_stored_row(
    state: ConfigState, client: MockVcoClient, name: str
) -> None:
    """The state's module *name*, the one the test edits, is what the store holds."""
    module = state.module(name)
    row = client.store.rows[MODULE]
    assert module.id_ == MODULE
    assert (module.raw_data, module.version) == (row["data"], row["version"])


def outbound_rules(new: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """The outbound rules of every segment of a firewall row."""
    for segment in new["data"]["segments"]:
        yield from segment["outbound"]


def issue_rule_ids(new: dict[str, Any], _: dict[str, Any] | None) -> None:
    """Give every firewall rule without a ``ruleLogicalId`` one, as the VCO does."""
    for rule in outbound_rules(new):
        _ = rule.setdefault("ruleLogicalId", ISSUED)


def revise_rule_ids(new: dict[str, Any], old: dict[str, Any] | None) -> None:
    """Give a firewall rule whose content changed a new ``ruleLogicalId``, as the VCO does."""
    assert old is not None
    before = {rule["ruleLogicalId"]: rule for rule in outbound_rules(old)}
    for rule in outbound_rules(new):
        if before.get(rule["ruleLogicalId"], rule) != rule:
            rule["ruleLogicalId"] = REVISED


def issue_link_ids(new: dict[str, Any], _: dict[str, Any] | None) -> None:
    """Give every WAN link without an ``internalId`` one, as the VCO does."""
    for item in new["data"]["links"]:
        _ = item.setdefault("internalId", str(U9))


def responders(row: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """The ICMP responders of every segment of a device settings row."""
    for segment in row["data"]["segments"]:
        yield from segment["routes"]["icmpResponders"]


def lower_case_ipv6(new: dict[str, Any], _: dict[str, Any] | None) -> None:
    """Store each ICMP responder's address in canonical (lower-case) form, as the VCO does."""
    for responder in responders(new):
        responder["ipAddress"] = responder["ipAddress"].lower()


def other_writer_edit(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller does not edit."""
    row["data"]["futureKey"] = 1


async def test_added_firewall_rule_gets_its_id_from_the_vco():
    raw = firewall(fw_rule("allow-web", ruleLogicalId="l1"))
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(issue_rule_ids)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={
            "block-ssh": add(
                {"action": {"allow_or_deny": "deny"}, "match": {"dport_low": 22}}
            )
        },
    )

    result = await pipeline.save(state)

    (change,) = result.server_changes[FIREWALL]
    assert str(change.path) == f"{OUTBOUND}[block-ssh].ruleLogicalId"
    assert (change.as_read, change.stored) == (MISSING, ISSUED)
    rules = {r.name: r for r in state.edge.firewall.segment(SEGMENT).outbound}
    assert rules["block-ssh"].rule_logical_id == ISSUED
    assert rules["allow-web"].rule_logical_id == "l1"
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "firewall")


async def test_rules_that_share_a_name_survive_a_revised_id():
    raw = firewall(
        fw_rule("web", ruleLogicalId="l1"), fw_rule("web", ruleLogicalId="l2")
    )
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(revise_rule_ids)
    pipeline = ConfigPipeline(client)
    # The two rules share a name, so the second is named by its alt key.
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={"l2": {"action": {"allow_or_deny": "deny"}}},
    )

    result = await pipeline.save(state)

    # Not the rule lost and another added: the ID is the one server change.
    (change,) = result.server_changes[FIREWALL]
    assert str(change.path) == f"{OUTBOUND}[#1].ruleLogicalId"
    assert (change.as_read, change.stored) == ("l2", REVISED)
    rules = state.edge.firewall.segment(SEGMENT).outbound
    assert [(r.rule_logical_id, r.action.allow_or_deny) for r in rules] == [
        ("l1", "allow"),
        (REVISED, "deny"),
    ]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "firewall")


async def test_added_wan_link_without_an_id_passes():
    state = make_state({"WAN": {"links": [link(U1, "Internet")]}})
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(issue_link_ids)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="wan",
        path="links",
        patch={"+": add({"name": "Backup", "type": "WIRED"})},
    )

    result = await pipeline.save(state)

    assert [(x.internal_id, x.name) for x in wan_links(state)] == [
        (U1, "Internet"),
        (U9, "Backup"),
    ]
    (change,) = result.server_changes[ConfigurationModuleName.WAN]
    assert str(change.path) == "links[#1].internalId"
    assert change.stored == str(U9)
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "WAN")


async def test_ipv6_stored_in_canonical_form_passes():
    responder = {
        "enabled": True,
        "ipAddress": "2001:db8::2",
        "name": "responder-v6",
        "logicalId": str(uuid.UUID(int=7)),
        "mode": "CONDITIONAL",
    }
    raw: dict[str, Any] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {
                "segment": desc(0, SEGMENT),
                "routes": {"static": [], "icmpResponders": [responder]},
            }
        ],
    }
    profile: dict[str, Any] = {"lan": {}, "segments": [{"segment": desc(0, SEGMENT)}]}
    state = make_state({"deviceSettings": raw}, {"deviceSettings": profile})
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(lower_case_ipv6)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="device_settings",
        segment=SEGMENT,
        path="routes.icmp_responders",
        patch={"responder-v6": {"ip_address": "2001:DB8::1"}},
    )

    result = await pipeline.save(state)

    (change,) = result.server_changes[ConfigurationModuleName.DEVICE_SETTINGS]
    assert str(change.path) == (
        'segments["Global Segment"].routes.icmpResponders[responder-v6].ipAddress'
    )
    assert (change.as_read, change.stored) == ("2001:db8::2", "2001:db8::1")
    routes = state.edge.device_settings.segment(SEGMENT).routes
    assert routes is not MISSING
    assert [r.ip_address for r in routes.icmp_responders] == ["2001:db8::1"]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "deviceSettings")


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_refused_reorder_and_edit_list_both(conditional_check: bool):
    raw = qos(
        *(qos_rule(name, ruleLogicalId=logical_id(n)) for n, name in enumerate("abcd"))
    )
    state = make_state({"QOS": raw}, {"QOS": qos()})
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(MODULE, other_writer_edit)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="qos",
        segment=SEGMENT,
        path="rules",
        patch={"d": move(first=True), "b": {"action": {"route_type": "edge2Cloud"}}},
    )

    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    rules = 'segments["Global Segment"].rules'
    lost = {str(change.path): change for change in exc.value.lost_edits}
    assert sorted(lost) == [rules, f"{rules}[b].action.routeType"]
    order = lost[rules]
    assert (order.as_read, order.intended, order.stored) == (
        ["a", "b", "c", "d"],
        ["d", "a", "b", "c"],
        ["a", "b", "c", "d"],
    )
    assert paths(exc.value.server_changes) == ["futureKey"]
    text = str(exc.value)
    assert f'  {rules}: read ["a", "b", "c", "d"], meant ["d", "a", "b", "c"]' in text
    assert '{"action"' not in text  # no rule body: one renders as {"action": ...
    assert qos_rules(state) == ["a", "b", "c", "d"]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "QOS")


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_reconciled_segment_is_a_lost_edit_when_refused(conditional_check: bool):
    state = make_state(
        {"firewall": firewall(fw_rule("a"))}, {"firewall": firewall(guest=True)}
    )
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(MODULE, other_writer_edit)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={"a": {"logging_enabled": True}},
    )
    (planned,) = state.plan().changed
    assert planned.reconciled_segments == ("Guest",)

    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    assert paths(exc.value.lost_edits) == [
        f"{OUTBOUND}[a].loggingEnabled",
        "segments[Guest]",
    ]
    assert paths(exc.value.server_changes) == ["futureKey"]
    assert [s.segment.name for s in state.edge.firewall.segments] == [SEGMENT]
    assert_state_is_the_stored_row(state, client, "firewall")
