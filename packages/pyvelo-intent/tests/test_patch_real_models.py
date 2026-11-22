"""The patch surface end to end on the real configuration models, through ``apply``.

Each test builds a state from module rows in wire form, applies one addressed
patch, and checks the typed model, the planned changes and the payload a save
sends.
"""

import copy
import uuid
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.link import WanLink
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.core.rebase import key_text
from pyvelo.testing import make_module_json
from pyvelo_intent import PatchError, add, delete, move
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient

SEGMENT = "Global Segment"
U1, U2 = uuid.UUID(int=1), uuid.UUID(int=2)


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def make_state(
    edge: dict[str, dict[str, Any]], profile: dict[str, dict[str, Any]] | None = None
) -> ConfigState:
    """An edge target whose modules have the given raw data, by module name."""

    def rows(modules: dict[str, dict[str, Any]], config_id: int) -> list[Any]:
        return [
            make_module_json(
                name,
                copy.deepcopy(data),
                module_id=config_id + i,
                configuration_id=config_id,
            )
            for i, (name, data) in enumerate(modules.items(), start=1)
        ]

    state = ConfigState(
        edge_config=EdgeConfiguration.model_validate(
            {"name": "Edge Specific Profile", "id": 10, "modules": rows(edge, 10)}
        ),
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": rows(profile or {}, 20)}
        ),
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
    )
    state.take_snapshot()
    return state


def change_paths(state: ConfigState) -> list[str]:
    (module,) = state.plan().changed
    return [str(c.path) for c in module.changes]


async def payload(state: ConfigState) -> dict[str, Any]:
    """The data a save of *state* sends for its one changed module."""
    client = MockVcoClient.for_state(state)
    _ = await ConfigPipeline(client).save(state)
    (request,) = client.update_requests
    return request.update.data


# --- firewall ---------------------------------------------------------------


def fw_rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "action": {"allow_or_deny": "allow"}, "match": {}, **extra}


def firewall(*rules: dict[str, Any], guest: bool = False) -> dict[str, Any]:
    segments: list[dict[str, Any]] = [
        {"segment": desc(0, SEGMENT), "outbound": list(rules)}
    ]
    if guest:
        segments.append({"segment": desc(2, "Guest"), "outbound": []})
    return {"segments": segments}


def outbound(state: ConfigState, segment: str = SEGMENT) -> list[str]:
    data = state.edge.firewall
    assert isinstance(data, FirewallData)
    return [r.name for r in data.segment(segment).outbound]


async def test_firewall_rule_add_change_delete_move():
    raw = firewall(
        fw_rule("a", ruleLogicalId="la", futureKey=1),
        fw_rule("b", ruleLogicalId="lb"),
        fw_rule("c", ruleLogicalId="lc"),
        fw_rule("d", ruleLogicalId="ld"),
    )
    patch: dict[str, object] = {
        "block-ssh": add(
            {"action": {"allow_or_deny": "deny"}, "match": {"dport_low": 22}},
            before="b",
        ),
        "b": {"logging_enabled": True},
        "c": delete(),
        "d": move(first=True),
    }

    async def apply(patch: dict[str, object]) -> ConfigState:
        state = make_state({"firewall": raw}, {"firewall": firewall()})
        return await ConfigPipeline(None).apply(
            state, module="firewall", segment=SEGMENT, path="outbound", patch=patch
        )

    state = await apply(patch)
    assert outbound(state) == ["d", "a", "block-ssh", "b"]
    rules = {r.name: r for r in state.edge.firewall.segment(SEGMENT).outbound}
    assert rules["b"].logging_enabled is True
    assert rules["block-ssh"].action.allow_or_deny == "deny"
    assert rules["block-ssh"].match.destination_port_low == 22
    # The move reorders paired items, which only the whole list can report.
    (module,) = state.plan().changed
    (change,) = module.changes
    assert str(change.path) == 'segments["Global Segment"].outbound'
    assert change.before == raw["segments"][0]["outbound"]
    assert isinstance(change.after, list)
    assert [r["name"] for r in change.after] == ["d", "a", "block-ssh", "b"]
    sent = (await payload(state))["segments"][0]["outbound"]
    assert [r["name"] for r in sent] == ["d", "a", "block-ssh", "b"]
    assert sent[1] == fw_rule("a", ruleLogicalId="la", futureKey=1)

    # Without the move, each operation is a change at its item.
    del patch["d"]
    state = await apply(patch)
    assert outbound(state) == ["a", "block-ssh", "b", "d"]
    assert change_paths(state) == [
        'segments["Global Segment"].outbound[b].loggingEnabled',
        'segments["Global Segment"].outbound[c]',
        'segments["Global Segment"].outbound[block-ssh]',
    ]


async def test_delete_one_of_two_firewall_rules_of_one_name():
    rules = [
        fw_rule("web", ruleLogicalId="l1", futureKey=1),
        fw_rule("web", ruleLogicalId="l2"),
        fw_rule("ssh", ruleLogicalId="l3", futureKey=3),
    ]
    state = make_state({"firewall": firewall(*rules)}, {"firewall": firewall()})
    # The two rules share a name, so the second is named by its alt key.
    _ = await ConfigPipeline(None).apply(
        state,
        module="firewall",
        segment=SEGMENT,
        path="outbound",
        patch={"l2": delete()},
    )
    (module,) = state.plan().changed
    (change,) = module.changes
    assert (str(change.path), change.before, change.after) == (
        'segments["Global Segment"].outbound[#1]',
        rules[1],
        MISSING,
    )
    sent = (await payload(state))["segments"][0]["outbound"]
    assert sent == [rules[0], rules[2]]


async def test_reading_a_later_inherited_segment_sends_nothing():
    profile: dict[str, Any] = {
        "segments": [
            {"segment": desc(0, SEGMENT), "outbound": []},
            {"segment": desc(1, "Guest"), "outbound": []},
            {"segment": desc(2, "Voice"), "outbound": []},
        ]
    }
    state = make_state({"firewall": firewall()}, {"firewall": profile})
    client = MockVcoClient.for_state(state)
    assert state.edge.firewall.segment("Voice").outbound == []  # a read
    assert state.plan().changed == ()
    result = await ConfigPipeline(client).save(state)
    assert (client.calls, result.modules_updated) == ([], [])


# --- QoS --------------------------------------------------------------------

QOS_ACTION: dict[str, Any] = {
    "edge2EdgeRouteAction": {},
    "edge2DataCenterRouteAction": {},
    "edge2CloudRouteAction": {},
    "QoS": {},
}


def qos_rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "match": {}, "action": copy.deepcopy(QOS_ACTION), **extra}


def qos(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"segments": [{"segment": desc(0, SEGMENT), "rules": list(rules)}]}


def qos_rules(state: ConfigState) -> list[str]:
    data = state.edge.qos
    assert isinstance(data, QosData)
    return [r.name for r in data.segment(SEGMENT).rules]


def logical_id(n: int) -> str:
    return str(uuid.UUID(int=100 + n))


async def test_qos_rule_add_change_delete_move():
    raw = qos(
        qos_rule("a", ruleLogicalId=logical_id(1), futureKey=1),
        qos_rule("b", ruleLogicalId=logical_id(2)),
        qos_rule("c", ruleLogicalId=logical_id(3)),
        qos_rule("d", ruleLogicalId=logical_id(4)),
    )
    patch: dict[str, object] = {
        "voice": add({"match": {"dport_low": 5060}, "action": QOS_ACTION}, before="b"),
        "b": {"action": {"route_type": "edge2Cloud"}},
        "c": delete(),
        "d": move(first=True),
    }

    async def apply(patch: dict[str, object]) -> ConfigState:
        state = make_state({"QOS": raw}, {"QOS": qos()})
        return await ConfigPipeline(None).apply(
            state, module="qos", segment=SEGMENT, path="rules", patch=patch
        )

    state = await apply(patch)
    assert qos_rules(state) == ["d", "a", "voice", "b"]
    rules = {r.name: r for r in state.edge.qos.segment(SEGMENT).rules}
    assert rules["b"].action.route_type == "edge2Cloud"
    assert rules["voice"].match.destination_port_low == 5060
    # The move reorders paired items, which only the whole list can report.
    (module,) = state.plan().changed
    (change,) = module.changes
    assert str(change.path) == 'segments["Global Segment"].rules'
    assert change.before == raw["segments"][0]["rules"]
    assert isinstance(change.after, list)
    assert [r["name"] for r in change.after] == ["d", "a", "voice", "b"]
    sent = (await payload(state))["segments"][0]["rules"]
    assert [r["name"] for r in sent] == ["d", "a", "voice", "b"]
    assert sent[1] == qos_rule("a", ruleLogicalId=logical_id(1), futureKey=1)

    # Without the move, each operation is a change at its item.
    del patch["d"]
    state = await apply(patch)
    assert qos_rules(state) == ["a", "voice", "b", "d"]
    assert change_paths(state) == [
        'segments["Global Segment"].rules[b].action.routeType',
        'segments["Global Segment"].rules[c]',
        'segments["Global Segment"].rules[voice]',
    ]


async def test_rename_a_qos_rule():
    raw = qos(
        qos_rule("old", ruleLogicalId=logical_id(1), futureKey=1),
        qos_rule("other", ruleLogicalId=logical_id(2)),
    )
    state = make_state({"QOS": raw}, {"QOS": qos()})
    _ = await ConfigPipeline(None).apply(
        state,
        module="qos",
        segment=SEGMENT,
        path="rules",
        patch={"old": {"name": "new"}},
    )
    assert qos_rules(state) == ["new", "other"]
    (module,) = state.plan().changed
    assert [(str(c.path), c.before, c.after) for c in module.changes] == [
        ('segments["Global Segment"].rules[old].name', "old", "new")
    ]
    sent = (await payload(state))["segments"][0]["rules"]
    assert sent[0] == {**raw["segments"][0]["rules"][0], "name": "new"}


# --- static routes ----------------------------------------------------------


def route(destination: str, **extra: object) -> dict[str, Any]:
    return {
        "destination": destination,
        "netmask": "255.255.255.0",
        "cidrPrefix": 24,
        "gateway": "192.168.1.1",
        "wanInterface": "GE3",
        **extra,
    }


def device_settings(*routes: dict[str, Any]) -> dict[str, Any]:
    return {
        "lan": {"networks": [], "interfaces": []},
        "segments": [{"segment": desc(0, SEGMENT), "routes": {"static": list(routes)}}],
    }


async def test_static_route_add_and_delete_by_composite_key():
    raw = device_settings(
        route("10.1.0.0", futureKey=1), route("10.2.0.0"), route("10.3.0.0")
    )
    state = make_state(
        {"deviceSettings": raw},
        {"deviceSettings": {"lan": {}, "segments": [{"segment": desc(0, SEGMENT)}]}},
    )
    _ = await ConfigPipeline(None).apply(
        state,
        module="device_settings",
        segment=SEGMENT,
        path="routes.static",
        patch={
            ("10.2.0.0", 24, "192.168.1.1", "GE3"): delete(),
            ("10.9.0.0", 24, "192.168.1.1", "GE3"): add({"netmask": "255.255.255.0"}),
        },
    )
    data = state.edge.device_settings
    assert isinstance(data, DeviceSettingsData)
    routes = data.segment(SEGMENT).routes
    assert routes is not MISSING
    assert [r.destination for r in routes.static] == [
        "10.1.0.0",
        "10.3.0.0",
        "10.9.0.0",
    ]
    added = routes.static[-1]
    assert (added.cidr_prefix, added.gateway, added.wan_interface) == (
        24,
        "192.168.1.1",
        "GE3",
    )
    assert sorted(change_paths(state)) == [
        'segments["Global Segment"].routes.static["10.2.0.0",24,"192.168.1.1",GE3]',
        'segments["Global Segment"].routes.static["10.9.0.0",24,"192.168.1.1",GE3]',
    ]
    sent = (await payload(state))["segments"][0]["routes"]["static"]
    assert sent[0] == route("10.1.0.0", futureKey=1)
    assert [r["destination"] for r in sent] == ["10.1.0.0", "10.3.0.0", "10.9.0.0"]


# --- WAN links --------------------------------------------------------------


def link(internal_id: uuid.UUID | None, name: str, **extra: object) -> dict[str, Any]:
    raw: dict[str, Any] = {"name": name, "type": "WIRED", "addressingVersion": "IPv4"}
    if internal_id is not None:
        raw["internalId"] = str(internal_id)
    return {**raw, **extra}


def wan_links(state: ConfigState) -> list[WanLink]:
    data = state.edge.wan
    assert isinstance(data, WanData)
    assert data.links is not MISSING
    return data.links


def link_path(internal_id: uuid.UUID, field: str) -> str:
    return f"links[{key_text((str(internal_id), 'IPv4'))}].{field}"


async def test_rename_a_wan_link():
    raw = {"links": [link(U1, "Internet", futureKey=1), link(U2, "MPLS")]}
    state = make_state({"WAN": raw})
    _ = await ConfigPipeline(None).apply(
        state, module="wan", path="links", patch={"MPLS": {"name": "Backup"}}
    )
    assert [x.name for x in wan_links(state)] == ["Internet", "Backup"]
    (module,) = state.plan().changed
    assert [(str(c.path), c.before, c.after) for c in module.changes] == [
        (link_path(U2, "name"), "MPLS", "Backup")
    ]
    sent = (await payload(state))["links"]
    assert sent == [link(U1, "Internet", futureKey=1), link(U2, "Backup")]


async def test_two_wan_links_of_one_name():
    raw = {"links": [link(U1, "Internet"), link(U2, "Internet")]}
    pipeline = ConfigPipeline(None)

    state = make_state({"WAN": raw})
    before = state.plan().render()
    with pytest.raises(PatchError) as exc:
        _ = await pipeline.apply(
            state, module="wan", path="links", patch={"Internet": {"isp": "x"}}
        )
    assert str(U1) in exc.value.message
    assert str(U2) in exc.value.message
    assert state.plan().render() == before

    state = make_state({"WAN": raw})
    _ = await pipeline.apply(
        state,
        module="wan",
        path="links",
        patch={(str(U2), "IPv4"): {"isp": "Carrier"}},
    )
    assert [x.isp for x in wan_links(state)] == [None, "Carrier"]
    assert change_paths(state) == [link_path(U2, "isp")]

    state = make_state({"WAN": raw})
    _ = await pipeline.apply(
        state,
        module="wan",
        path="links",
        patch={"+": add({"name": "Internet", "type": "WIRED"})},
    )
    links = wan_links(state)
    assert [(x.internal_id, x.name) for x in links] == [
        (U1, "Internet"),
        (U2, "Internet"),
        (MISSING, "Internet"),
    ]
    sent = (await payload(state))["links"]
    assert sent[:2] == raw["links"]
    assert len(sent) == 3
    assert sent[2]["name"] == "Internet"
    assert "internalId" not in sent[2]


# --- segments ---------------------------------------------------------------


async def test_select_a_segment_by_segment_id():
    raw = firewall(fw_rule("a"), guest=True)
    state = make_state({"firewall": raw}, {"firewall": firewall(guest=True)})
    _ = await ConfigPipeline(None).apply(
        state,
        module="firewall",
        segment="2",
        path="outbound",
        patch={"block": add({"action": {"allow_or_deny": "deny"}, "match": {}})},
    )
    assert outbound(state, "Guest") == ["block"]
    assert outbound(state) == ["a"]
    assert change_paths(state) == ["segments[Guest].outbound[block]"]


def bgp_settings(bgp: dict[str, Any]) -> dict[str, Any]:
    return {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {"segment": desc(0, SEGMENT), "routes": {"static": []}, "bgp": bgp}
        ],
    }


async def test_a_patch_inside_inherited_bgp_is_listed_on_the_plan_and_copies_nothing():
    edge_bgp: dict[str, Any] = {
        "override": False,
        "enabled": False,
        "ASN": "",
        "neighbors": [],
        "filters": [],
    }
    profile_bgp: dict[str, Any] = {
        "enabled": True,
        "ASN": "65000",
        "filters": [],
        "neighbors": [{"neighborIp": "10.0.0.1", "neighborAS": "65001"}],
    }
    state = make_state(
        {"deviceSettings": bgp_settings(edge_bgp)},
        {"deviceSettings": bgp_settings(profile_bgp)},
    )
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="device_settings",
        segment=SEGMENT,
        path="bgp.neighbors",
        patch={"10.0.0.2": add({"neighbor_as": "65002"})},
    )
    preview = await pipeline.save(state, dry_run=True)
    assert preview.plan is not None
    assert preview.plan.render().splitlines()[-1] == (
        '  override turned on: segments["Global Segment"].bgp (the edge\'s own '
        "values apply there from now on, and the profile's no longer do)"
    )
    _ = await pipeline.save(state)
    (request,) = client.update_requests
    sent = request.update.data["segments"][0]["bgp"]
    assert (sent["override"], sent["enabled"], sent["ASN"]) == (True, False, "")
    assert [n["neighborIp"] for n in sent["neighbors"]] == ["10.0.0.2"]
