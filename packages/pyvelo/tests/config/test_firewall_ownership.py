"""The firewall through ``ConfigPipeline.apply`` and ``state.effective``.

Patches on inherited values start from the profile.
"""

import copy
import uuid
from typing import Any, cast, final

import pytest
from profile_state import profile_state
from pydantic import MISSING
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.profile_segments import module_template_value
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.config import PatchError, add
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.address_group import AddressGroupResolver
from pyvelo.config.resolvers.service_group import ServiceGroupResolver
from pyvelo.config.state import ConfigState
from pyvelo.core import Effective, Origin, Resolved, effective_dump
from pyvelo.testing import MockVcoClient, make_module_json, make_segment_resolver
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    PROFILE_FW,
    desc,
)

PROFILE_SERVICES: dict[str, Any] = {"loggingEnabled": True, "ssh": {"enabled": False}}
DENY: dict[str, Any] = {"action": {"allow_or_deny": "deny"}, "match": {}}


def global_segment(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"segment": desc(0, "Global Segment"), "outbound": list(rules)}


def state_of(
    edge: dict[str, Any] | None, profile: dict[str, Any] | None = None
) -> ConfigState:
    """An edge state whose edge firewall data is *edge* (no row for None).

    Its profile's firewall data is *profile*.
    """
    edge_rows = (
        []
        if edge is None
        else [
            make_module_json(
                "firewall",
                copy.deepcopy(edge),
                module_id=1,
                configuration_id=10,
                version="e1",
            )
        ]
    )
    profile_raw = copy.deepcopy(PROFILE_FW if profile is None else profile)
    state = ConfigState(
        edge_config=EdgeConfiguration.model_validate(
            {"name": "Edge Specific Profile", "id": 10, "modules": edge_rows}
        ),
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {
                "name": "P",
                "id": 20,
                "modules": [
                    make_module_json(
                        "firewall", profile_raw, module_id=2, configuration_id=20
                    )
                ],
            }
        ),
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
    )
    state.take_snapshot()
    return state


def firewall_plan(state: ConfigState) -> ModulePlan:
    (plan,) = state.plan().changed
    assert plan.name == "firewall"
    return plan


def changed_paths(state: ConfigState) -> list[str]:
    return [str(c.path) for c in firewall_plan(state).changes]


def change_after(plan: ModulePlan, path: str) -> object:
    (found,) = [c for c in plan.changes if str(c.path) == path]
    return found.after


# --- a patch on an inherited value starts from the profile ------------------


async def test_a_patch_into_an_inherited_section_the_profile_lacks_copies_the_display_default() -> (  # noqa: E501 (a test's name)
    None
):
    state = state_of({"segments": [global_segment()]})
    await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"services": {"ssh": {"enabled": True}}}
    )
    default = module_template_value("firewall", "services")
    assert isinstance(default, dict)
    expected = copy.deepcopy(default)
    expected["ssh"]["enabled"] = True
    assert changed_paths(state) == ["services"]
    assert change_after(firewall_plan(state), "services") == expected
    assert default["ssh"]["enabled"] is False  # the template is not changed


async def test_a_patch_into_an_inherited_section_copies_the_profiles() -> None:
    state = state_of(
        {"segments": [global_segment()]}, {**PROFILE_FW, "services": PROFILE_SERVICES}
    )
    await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"services": {"ssh": {"enabled": True}}}
    )
    assert changed_paths(state) == ["services"]
    after = change_after(firewall_plan(state), "services")
    assert after == {**PROFILE_SERVICES, "ssh": {"enabled": True}}
    # The profile itself is as it was.
    assert state.profile.firewall.services.logging_enabled is True
    assert state.profile.firewall.services.ssh.enabled is False  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


async def test_null_inherits_and_plan_shows_the_removal() -> None:
    state = state_of({"segments": [global_segment()], "syslog_forwarding": True})
    await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"syslog_forwarding": None}
    )
    plan = firewall_plan(state)
    assert [str(c.path) for c in plan.changes] == ["syslog_forwarding"]
    assert change_after(plan, "syslog_forwarding") is MISSING
    assert "syslog_forwarding" not in plan.data


async def test_a_member_patch_localizes_the_stateful_group() -> None:
    profile_settings = {
        "establishedTcpFlowTimeout": 7000,
        "nonEstablishedTcpFlowTimeout": 200,
        "udpFlowTimeout": 111,
        "otherFlowTimeout": 50,
    }
    state = state_of(
        {"segments": [global_segment()]},
        {**PROFILE_FW, "statefulFirewallSettings": profile_settings},
    )
    await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"stateful_firewall_enabled": False}
    )
    plan = firewall_plan(state)
    assert [str(c.path) for c in plan.changes] == [
        "stateful_firewall_enabled",
        "statefulFirewallSettings",
        "statefulFirewallSettingsV6",
    ]
    assert plan.data["stateful_firewall_enabled"] is False
    assert plan.data["statefulFirewallSettings"] == profile_settings
    assert plan.data["statefulFirewallSettingsV6"] == module_template_value(
        "firewall", "statefulFirewallSettingsV6"
    )
    assert state.edge.firewall.stateful.is_local is True


async def test_a_failed_patch_leaves_a_localized_group_inherited() -> None:
    state = state_of({"segments": [global_segment()]})
    before = state.plan().render()
    with pytest.raises(PatchError, match="syslog_forwarding"):
        await ConfigPipeline(client=None).apply(
            state,
            module="firewall",
            patch={"stateful_firewall_enabled": False, "syslog_forwarding": "abc"},
        )
    assert state.edge.firewall.stateful.is_local is False
    assert state.edge.firewall.stateful_firewall_enabled.stored is MISSING
    assert state.plan().changed == ()
    assert state.plan().render() == before


async def test_owned_intents_on_a_detached_layer_raise() -> None:
    state = state_of({"segments": [global_segment()], "syslog_forwarding": True})
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    owned = state.edge.firewall.syslog_forwarding
    await pipeline.apply(state, module="firewall", patch={"firewall_enabled": False})
    await pipeline.save(state)
    assert state.edge.firewall.syslog_forwarding is not owned
    for intent in (lambda: owned.set_local(True), owned.inherit, owned.localize):
        with pytest.raises(
            DetachedObjectError, match="take it again from the module as stored"
        ):
            intent()
    assert owned.stored is True  # reading a detached object is fine


# --- the effective view -----------------------------------------------------


def resolved(view: FirewallData[Effective]) -> dict[str, Resolved[object]]:
    return effective_dump(view)[1]


async def test_the_effective_view_follows_the_layers() -> None:
    edge_rule = {"name": "edge-rule", **DENY}
    state = state_of(
        {
            "segments": [global_segment(edge_rule)],
            "syslog_forwarding": True,
            "firewall_enabled": True,
        },
        {**PROFILE_FW, "firewall_enabled": False},
    )
    view = state.effective.firewall
    assert view.syslog_forwarding.origin is Origin.EDGE
    assert view.syslog_forwarding.value is True
    # The profile that has the firewall off turns it off for every edge.
    assert view.firewall_enabled.origin is Origin.PROFILE
    assert view.firewall_enabled.value is False
    assert view.atp_enabled.origin is Origin.FACTORY
    glob = view.get_segment(name="Global Segment")
    assert [(r.origin, r.value.name) for r in glob.outbound] == [
        (Origin.EDGE, "edge-rule"),
        (Origin.PROFILE, "allow-all"),
    ]
    # Computed on each access: an edit shows in the next view.
    state.edge.firewall.syslog_forwarding.inherit()
    assert state.effective.firewall.syslog_forwarding.origin is Origin.FACTORY


async def test_the_effective_view_of_an_edge_without_a_row_creates_nothing() -> None:
    state = state_of(None, {**PROFILE_FW, "syslog_forwarding": True})
    before = state.plan().render()
    modules_before = state.edge_config.modules if state.edge_config else None
    view = state.effective.firewall
    origins = {r.origin for r in resolved(view).values()}
    assert origins <= {Origin.PROFILE, Origin.FACTORY}
    assert view.syslog_forwarding == Resolved(Origin.PROFILE, True, "syslog_forwarding")
    assert view.services.origin is Origin.FACTORY
    assert state.find_module("firewall") is None
    assert state.edge_config is not None
    assert state.edge_config.modules == modules_before
    assert state.plan().render() == before


def test_a_profile_state_has_no_effective_view() -> None:
    state = ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": []}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    with pytest.raises(ValueError, match="no edge layer"):
        _ = state.effective


async def test_a_patch_sets_a_rules_logging_flags() -> None:
    stored = {"name": "block", "match": {}, "action": {"allow_or_deny": "deny"}}
    state = state_of({"segments": [global_segment(stored)]})
    await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {"block": {"hostedLoggingEnabled": True}}
                }
            }
        },
    )
    path = 'segments["Global Segment"].outbound[block].hostedLoggingEnabled'
    assert changed_paths(state) == [path]
    assert change_after(firewall_plan(state), path) is True


# --- a module the edge lacks ------------------------------------------------


async def test_a_missing_edge_module_inserts_only_local_content() -> None:
    state = state_of(None, {**PROFILE_FW, "syslog_forwarding": True})
    await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        patch={
            "syslog_forwarding": False,
            "segments": {"Global Segment": {"outbound": {"block": add(DENY)}}},
        },
    )
    (plan,) = state.plan().changed
    assert plan.operation == "insert"
    assert set(plan.data) == {"segments", "syslog_forwarding"}
    assert plan.data["syslog_forwarding"] is False
    names = [s["segment"]["name"] for s in plan.data["segments"]]
    assert names == ["Global Segment", "Guest"]  # the reconciled placeholders
    assert [r["name"] for r in plan.data["segments"][0]["outbound"]] == ["block"]
    assert plan.data["segments"][1]["outbound"] == []
    assert "allow-all" not in str(plan.data)


async def test_a_patch_on_an_inherited_object_of_a_missing_module_copies_it_into_the_insert() -> (  # noqa: E501 (a test's name)
    None
):
    profile_segments = copy.deepcopy(PROFILE_FW["segments"])
    profile_segments[0]["outbound"][0]["ruleLogicalId"] = str(uuid.UUID(int=0x55))
    state = state_of(
        None,
        {**PROFILE_FW, "segments": profile_segments, "services": PROFILE_SERVICES},
    )
    await ConfigPipeline(client=None).apply(
        state, module="firewall", patch={"services": {"ssh": {"enabled": True}}}
    )
    (plan,) = state.plan().changed
    assert plan.operation == "insert"
    assert set(plan.data) == {"segments", "services"}
    # The profile's services, with the edit.
    assert plan.data["services"] == {**PROFILE_SERVICES, "ssh": {"enabled": True}}
    # The reconciled placeholders, with nothing the profile assigned.
    names = [s["segment"]["name"] for s in plan.data["segments"]]
    assert names == ["Global Segment", "Guest"]
    assert [s["outbound"] for s in plan.data["segments"]] == [[], []]
    assert "ruleLogicalId" not in str(plan.data)
    # The profile is as it was.
    assert state.profile.firewall.services.ssh.enabled is False  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


# --- names resolve through the converted firewall ---------------------------

PRINTERS = uuid.UUID(int=0x77)
WEB = uuid.UUID(int=0x88)


@final
class FakeGroup:
    def __init__(self, id_: int, name: str, logical_id: uuid.UUID) -> None:
        self.id_: int = id_
        self.name: str = name
        self.logical_id: uuid.UUID = logical_id


@final
class ObjectGroupClient:
    """Answers ``get_object_groups``.

    The answer holds the address group ``printers`` and the service group ``web``.
    """

    @property
    def protocol(self) -> VcoClientProtocol:
        return cast("VcoClientProtocol", cast("object", self))

    async def get_object_groups(self, request: GetObjectGroups) -> list[FakeGroup]:
        if request.type_ == "address_group":
            return [FakeGroup(7, "printers", PRINTERS)]
        return [FakeGroup(8, "web", WEB)]


async def register_group_resolvers(registry: ResolverRegistry) -> None:
    client = ObjectGroupClient().protocol
    addresses = AddressGroupResolver(client)
    services = ServiceGroupResolver(client)
    for resolver in (addresses, services):
        await resolver.load(ResolverContext(enterprise_id=5))
    registry.register(addresses)
    registry.register(services)


async def with_resolvers(state: ConfigState) -> ConfigState:
    """*state* with segment, address group and service group resolvers."""
    registry = ResolverRegistry()
    registry.register(
        await make_segment_resolver(
            [
                {"name": "Global Segment", "segment_id": 0},
                {"name": "Guest", "segment_id": 2},
            ]
        )
    )
    await register_group_resolvers(registry)
    state.resolvers = registry
    return state


NAMED_MATCH: dict[str, Any] = {
    "action": {"allow_or_deny": "deny"},
    "match": {
        "source_address_group": "printers",
        "destination_port_group": "web",
    },
}


def block_rule(module: FirewallData[Any], segment: str) -> Any:
    (rule,) = [
        r for r in module.get_segment(name=segment).outbound if r.name == "block"
    ]
    return rule


async def test_names_resolve_through_the_converted_firewall_at_an_edge() -> None:
    state = await with_resolvers(state_of({"segments": [global_segment()]}))
    await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "guest": {"outbound": {"block": add(NAMED_MATCH)}},
                "global segment": {"outbound": {"block": add(NAMED_MATCH)}},
            }
        },
    )
    firewall = state.edge.firewall
    assert [s.segment.segment_id for s in firewall.segments] == [0, 2]
    for segment in ("Global Segment", "Guest"):
        rule = block_rule(firewall, segment)
        assert rule.match.source_address_group == PRINTERS
        assert rule.match.destination_port_group == WEB
    plan = firewall_plan(state)
    guest = next(s for s in plan.data["segments"] if s["segment"]["name"] == "Guest")
    (written,) = [r for r in guest["outbound"] if r["name"] == "block"]
    assert written["match"]["sAddressGroup"] == str(PRINTERS)
    assert written["match"]["dPortGroup"] == str(WEB)


async def test_names_resolve_through_the_converted_firewall_at_a_profile() -> None:
    state = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )
    await register_group_resolvers(state.resolvers)
    await ConfigPipeline(client=None).apply(
        state,
        module="firewall",
        patch={"segments": {"guest": {"outbound": {"block": add(NAMED_MATCH)}}}},
    )
    firewall = state.profile.firewall
    rule = block_rule(firewall, "Guest")
    assert rule.match.source_address_group == PRINTERS
    assert rule.match.destination_port_group == WEB
    assert firewall.get_segment(name="Guest").segment.segment_id == 2
