"""Write-back on the real configuration models, through ``ConfigPipeline``.

Each test builds an edge state from module rows in wire form, edits it through
``apply`` and saves it to a ``ModuleStore`` (``MockVcoClient.for_state``),
whose hooks store values as the VCO does. It then checks the state, the
save's ``server_changes`` (or the error's lists) and the stored row. The edge
configuration is 10 and its first module 11; the profile is 20.
"""

import json
import uuid
from collections.abc import Iterable
from typing import Any, cast

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import ConfigConflictError, WriteNotAppliedError
from pyvelo.config import add, at, delete, move
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core.response_check import PlannedChange, ServerChange
from pyvelo.testing import MockVcoClient
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


def lower_case_v6_routes(new: dict[str, Any], _: dict[str, Any] | None) -> None:
    """Store each IPv6 static route's addresses in canonical (lower-case) form, as the VCO does."""
    for segment in new["data"]["segments"]:
        for route in segment["routes"]["staticV6"]:
            route["destination"] = route["destination"].lower()
            route["gateway"] = route["gateway"].lower()


def other_writer_edit(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller does not edit."""
    row["data"]["futureKey"] = 1


async def test_added_firewall_rule_gets_its_id_from_the_vco() -> None:
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


async def test_rules_that_share_a_name_survive_a_revised_id() -> None:
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


async def test_added_wan_link_without_an_id_passes() -> None:
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


async def test_ipv6_stored_in_canonical_form_passes() -> None:
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


async def test_ipv6_keyed_static_route_stored_in_canonical_form_passes() -> None:
    raw: dict[str, Any] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {
                "segment": desc(0, SEGMENT),
                "routes": {"static": [], "staticV6": []},
            }
        ],
    }
    profile: dict[str, Any] = {"lan": {}, "segments": [{"segment": desc(0, SEGMENT)}]}
    state = make_state({"deviceSettings": raw}, {"deviceSettings": profile})
    client = MockVcoClient.for_state(state)
    client.store.on_store.append(lower_case_v6_routes)
    pipeline = ConfigPipeline(client)
    # The address text is part of the route's key.
    _ = await pipeline.apply(
        state,
        module="device_settings",
        segment=SEGMENT,
        path="routes.static_v6",
        patch={("2001:DB8:1::", 64, "FE80::1", "GE3"): add({"netmask": ""})},
    )

    result = await pipeline.save(state)

    route = (
        'segments["Global Segment"].routes.staticV6["2001:DB8:1::",64,"FE80::1",GE3]'
    )
    changes = result.server_changes[ConfigurationModuleName.DEVICE_SETTINGS]
    assert paths(changes) == [f"{route}.destination", f"{route}.gateway"]
    assert [
        (c.as_read, c.stored) for c in changes if c.path.parts[-1] == "destination"
    ] == [(MISSING, "2001:db8:1::")]
    routes = state.edge.device_settings.segment(SEGMENT).routes
    assert routes is not MISSING
    assert [(r.destination, r.gateway) for r in routes.static_v6] == [
        ("2001:db8:1::", "fe80::1")
    ]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "deviceSettings")


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_refused_reorder_and_edit_list_both(conditional_check: bool) -> None:
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
    assert (
        f'  {rules}: order: d first: read ["a", "b", "c", "d"], '
        'meant ["d", "a", "b", "c"], stored ["a", "b", "c", "d"]'
    ) in text
    assert '{"action"' not in text  # no rule body: one renders as {"action": ...
    assert qos_rules(state) == ["a", "b", "c", "d"]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "QOS")


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_refused_delete_of_a_same_named_rule_is_the_one_lost_edit(
    conditional_check: bool,
) -> None:
    rules = [
        fw_rule("web", ruleLogicalId="l1", futureKey=1),
        fw_rule("web", ruleLogicalId="l2"),
        fw_rule("ssh", ruleLogicalId="l3", futureKey=3),
    ]
    state = make_state({"firewall": firewall(*rules)}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(MODULE, other_writer_edit)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={"l2": delete()},
    )

    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    (lost,) = exc.value.lost_edits
    assert (str(lost.path), lost.as_read, lost.intended, lost.stored) == (
        f"{OUTBOUND}[#1]",
        rules[1],
        MISSING,
        rules[1],
    )
    assert paths(exc.value.server_changes) == ["futureKey"]
    assert state.plan().changed == ()
    assert_state_is_the_stored_row(state, client, "firewall")


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_reconciled_segment_is_a_lost_edit_when_refused(
    conditional_check: bool,
) -> None:
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


# --- redoing lost edits ---------------------------------------------------------

DENY: dict[str, Any] = {"action": {"allow_or_deny": "deny"}, "match": {"dport_low": 22}}


def outbound_ops(patch: dict[str, object], segment: str = SEGMENT) -> dict[str, Any]:
    """The item operations *patch* has for the outbound rules of *segment*."""
    segments = cast("dict[str, dict[str, dict[str, Any]]]", patch["segments"])
    return segments[segment]["outbound"]


def stored_rules(client: MockVcoClient, segment: int = 0) -> list[dict[str, Any]]:
    """The outbound rules of a segment of the firewall row the store holds."""
    return client.store.rows[MODULE]["data"]["segments"][segment]["outbound"]


@pytest.mark.parametrize("conditional_check", [False, True])
async def test_redo_patch_redoes_a_place_and_a_move(conditional_check: bool) -> None:
    raw = firewall(
        fw_rule("block-ssh", ruleLogicalId="l1"),
        fw_rule("allow-branch-erp", ruleLogicalId="l2"),
    )
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={
            "block-rdp": add(DENY, first=True),
            "block-ssh": {"logging_enabled": True},
            "allow-branch-erp": move(first=True),
        },
    )
    (first,) = state.plan().changed
    client.store.other_writer(MODULE, other_writer_edit)

    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.lost_edits) == [
        OUTBOUND,
        f"{OUTBOUND}[block-rdp]",
        f"{OUTBOUND}[block-ssh].loggingEnabled",
    ]
    assert error.not_redone() == ()
    patch = error.redo_patch()
    assert json.loads(json.dumps(patch)) == patch  # plain JSON, as a patch must be
    _ = await pipeline.apply(state, module=error.module_name, patch=patch)
    (second,) = state.plan().changed
    assert second.changes == first.changes
    assert second.render() == first.render()

    _ = await pipeline.save(state)
    assert [(r["name"], r.get("loggingEnabled")) for r in stored_rules(client)] == [
        ("allow-branch-erp", None),
        ("block-rdp", False),
        ("block-ssh", True),
    ]
    assert state.plan().changed == ()


async def test_redo_patch_leaves_a_clash_out_unless_asked() -> None:
    raw = firewall(fw_rule("allow-web", ruleLogicalId="l1", match={"dport_high": 443}))
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state, conditional_check=True)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={
            "block-ssh": add(DENY, before="allow-web"),
            "allow-web": {"match": {"dport_high": 8080}},
        },
    )

    def same_port(row: dict[str, Any]) -> None:
        row["data"]["segments"][0]["outbound"][0]["match"]["dport_high"] = 8443

    client.store.other_writer(MODULE, same_port)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.not_redone()) == [f"{OUTBOUND}[allow-web].match.dport_high"]
    assert list(outbound_ops(error.redo_patch())) == ["block-ssh"]
    assert error.not_redone(include_clashes=True) == ()
    with_clashes = outbound_ops(error.redo_patch(include_clashes=True))
    assert list(with_clashes) == ["allow-web", "block-ssh"]
    assert with_clashes["allow-web"] == {"match": {"destination_port_high": 8080}}

    _ = await pipeline.apply(state, module=error.module_name, patch=error.redo_patch())
    _ = await pipeline.save(state)
    assert [(r["name"], r["match"]["dport_high"]) for r in stored_rules(client)] == [
        ("block-ssh", -1),
        ("allow-web", 8443),
    ]


async def test_redo_patch_names_an_item_as_the_stored_list_does() -> None:
    # Two rules share a name, so each is named by its position.
    raw = firewall(
        fw_rule("d", ruleLogicalId="l1"),
        fw_rule("d", ruleLogicalId="l2"),
        fw_rule("x", ruleLogicalId="l3"),
    )
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state, conditional_check=True)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={at(1): {"logging_enabled": True}},
    )

    def theirs_first(row: dict[str, Any]) -> None:
        rules = row["data"]["segments"][0]["outbound"]
        rules.insert(0, fw_rule("theirs", ruleLogicalId="l9"))

    client.store.other_writer(MODULE, theirs_first)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.lost_edits) == [f"{OUTBOUND}[#1].loggingEnabled"]
    assert outbound_ops(error.redo_patch()) == {"#2": {"logging_enabled": True}}
    _ = await pipeline.apply(state, module=error.module_name, patch=error.redo_patch())
    _ = await pipeline.save(state)
    assert [
        (r["ruleLogicalId"], r.get("loggingEnabled")) for r in stored_rules(client)
    ] == [("l9", None), ("l1", None), ("l2", True), ("l3", None)]


async def test_redo_patch_leaves_out_an_add_whose_place_is_gone() -> None:
    raw = firewall(
        fw_rule("block-ssh", ruleLogicalId="l1"),
        fw_rule("allow-branch-erp", ruleLogicalId="l2"),
    )
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state, conditional_check=True)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={
            "block-rdp": add(DENY, after="block-ssh"),
            "allow-branch-erp": {"logging_enabled": True},
        },
    )

    def remove_block_ssh(row: dict[str, Any]) -> None:
        del row["data"]["segments"][0]["outbound"][0]

    client.store.other_writer(MODULE, remove_block_ssh)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.not_redone()) == [f"{OUTBOUND}[block-rdp]"]
    assert outbound_ops(error.redo_patch()) == {
        "allow-branch-erp": {"logging_enabled": True}
    }


async def test_redo_patch_redoes_the_change_made_in_a_reconciled_segment() -> None:
    state = make_state(
        {"firewall": firewall(fw_rule("a", ruleLogicalId="l1"))},
        {"firewall": firewall(guest=True)},
    )
    client = MockVcoClient.for_state(state, conditional_check=True)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment="Guest",
        path="outbound",
        patch={"r": add(DENY)},
    )
    (first,) = state.plan().changed
    client.store.other_writer(MODULE, other_writer_edit)
    with pytest.raises(ConfigConflictError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.lost_edits) == ["segments[Guest]"]
    patch = error.redo_patch()
    assert list(outbound_ops(patch, "Guest")) == ["r"]
    assert error.not_redone() == ()
    _ = await pipeline.apply(state, module=error.module_name, patch=patch)
    (second,) = state.plan().changed
    assert second.changes == first.changes
    assert second.reconciled_segments == ("Guest",)
    _ = await pipeline.save(state)
    assert [r["name"] for r in stored_rules(client, segment=1)] == ["r"]


async def test_an_added_rule_stored_in_another_place_is_redone_as_a_move() -> None:
    raw = firewall(fw_rule("a", ruleLogicalId="l1"), fw_rule("b", ruleLogicalId="l2"))
    state = make_state({"firewall": raw}, {"firewall": firewall()})
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)

    def new_rules_last(new: dict[str, Any], _: dict[str, Any] | None) -> None:
        """Store the rules that have no ID yet after the others."""
        rules: list[dict[str, Any]] = new["data"]["segments"][0]["outbound"]
        rules.sort(key=lambda rule: "ruleLogicalId" not in rule)

    client.store.on_store.append(new_rules_last)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={"x": add(DENY, first=True)},
    )
    with pytest.raises(WriteNotAppliedError) as exc:
        _ = await pipeline.save(state)

    error = exc.value
    assert paths(error.applied) == [f"{OUTBOUND}[x]"]
    (order,) = error.lost_edits
    assert (str(order.path), order.is_order, order.clash) == (OUTBOUND, True, False)
    assert (order.intended, order.stored) == (["x", "a", "b"], ["a", "b", "x"])
    assert error.redo_patch() == {
        "segments": {SEGMENT: {"outbound": {"x": {"_position": "first"}}}}
    }
    client.store.on_store.clear()
    _ = await pipeline.apply(state, module=error.module_name, patch=error.redo_patch())
    _ = await pipeline.save(state)
    assert [r["name"] for r in stored_rules(client)] == ["x", "a", "b"]
