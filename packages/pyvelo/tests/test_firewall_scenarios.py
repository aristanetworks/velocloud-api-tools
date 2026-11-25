"""The firewall's ownership scenarios, each through ``plan_module`` on edge modules bound to a profile."""

import copy
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import GroupOwnershipError
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall import (
    ConsoleService,
    FirewallOutboundRule,
    OutboundRuleMatch,
    StatefulFirewallSettings,
    StatefulLocal,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.core import ConfigContext, Edge, Profile
from pyvelo.core.effective import Origin, effective_view
from pyvelo.core.layers import Effective
from pyvelo.testing import store_of

from ._type_support import MockVcoClientFactory
from .write_helpers import fw

EDGE = ConfigContext.EDGE

STATEFUL: dict[str, Any] = {
    "statefulFirewallSettings": {"udpFlowTimeout": 111},
    "statefulFirewallSettingsV6": {"udpFlowTimeout": 222},
    "stateful_firewall_enabled": True,
}
PROFILE_STATEFUL: dict[str, Any] = {
    "statefulFirewallSettings": {"udpFlowTimeout": 600},
    "statefulFirewallSettingsV6": {"udpFlowTimeout": 700},
    "stateful_firewall_enabled": False,
}


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def bound(
    edge_raw: dict[str, Any], profile_raw: dict[str, Any] | None = None
) -> tuple[FirewallConfigurationModule[Edge], FirewallConfigurationModule[Profile]]:
    """An edge module of *edge_raw* bound to a profile module of *profile_raw*, and that one."""
    edge = fw({"segments": [], **edge_raw}, layer=Edge)
    profile = fw({"segments": [], **(profile_raw or {})}, layer=Profile, module_id=2)
    edge.data.bind_profile(profile.raw_data)
    return edge, profile


def payload(edge: Any, profile: Any) -> dict[str, Any]:
    return plan_module(edge, level=EDGE, profile=profile).data


def test_an_unrelated_edit_leaves_an_inherited_section_absent() -> None:
    edge, profile = bound({})
    assert edge.data.services.stored is MISSING
    edge.data.syslog_forwarding.set_local(True)
    data = payload(edge, profile)
    assert data == {"segments": [], "syslog_forwarding": True}
    assert "services" not in data


async def test_an_explicit_local_false_survives_a_reload(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    edge, _ = bound({"syslog_forwarding": False})
    owned = edge.data.syslog_forwarding
    assert (owned.is_local, owned.local) == (True, False)
    edge.data.firewall_logging_enabled.set_local(True)  # an unrelated edit
    client = await mock_vco_client(store_of(edge))
    stored = await client.update_module(edge, edge.data)
    again = stored.data.syslog_forwarding
    assert (again.stored, again.is_local, again.local) == (False, True, False)
    raw = stored.raw_data
    assert isinstance(raw, dict)
    assert raw["syslog_forwarding"] is False
    assert raw["firewall_logging_enabled"] is True


def test_a_stored_null_is_kept_and_does_not_count_as_local() -> None:
    edge, profile = bound({"syslog_forwarding": None})
    owned = edge.data.syslog_forwarding
    assert owned.stored is None
    assert owned.is_local is False
    assert owned.local is MISSING
    # The profile stores nothing for it either: the effective value is the display default.
    view = effective_view(edge.data, profile.data, FirewallData[Effective])
    assert view.syslog_forwarding.origin is Origin.FACTORY
    stores = effective_view(
        edge.data,
        bound({}, {"syslog_forwarding": True})[1].data,
        FirewallData[Effective],
    )
    assert (stores.syslog_forwarding.origin, stores.syslog_forwarding.value) == (
        Origin.PROFILE,
        True,
    )
    # No edit: nothing is planned. An unrelated edit keeps the null.
    assert plan_module(edge, level=EDGE, profile=profile).operation == "none"
    edge.data.firewall_logging_enabled.set_local(False)
    assert payload(edge, profile) == {
        "segments": [],
        "syslog_forwarding": None,
        "firewall_logging_enabled": False,
    }


def test_inheriting_the_stateful_group_removes_exactly_its_three_keys() -> None:
    edge, profile = bound(
        {**STATEFUL, "syslog_forwarding": True, "futureKey": [1]}, PROFILE_STATEFUL
    )
    assert edge.data.stateful.is_local
    edge.data.stateful.inherit()
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert plan.data == {"segments": [], "syslog_forwarding": True, "futureKey": [1]}
    assert sorted(str(c.path) for c in plan.changes) == sorted(
        [
            "statefulFirewallSettings",
            "statefulFirewallSettingsV6",
            "stateful_firewall_enabled",
        ]
    )


def test_setting_stateful_enabled_false_while_local_keeps_the_settings() -> None:
    edge, profile = bound(STATEFUL, PROFILE_STATEFUL)
    edge.data.stateful_firewall_enabled.set_local(False)
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert plan.data == {**STATEFUL, "segments": [], "stateful_firewall_enabled": False}
    assert [str(c.path) for c in plan.changes] == ["stateful_firewall_enabled"]
    assert edge.data.stateful.is_local


def test_setting_one_stateful_member_while_inherited_refuses() -> None:
    edge, _ = bound({}, PROFILE_STATEFUL)
    with pytest.raises(GroupOwnershipError):
        edge.data.stateful_firewall_enabled.set_local(False)
    # The group, made local first, is the profile's values.
    edge.data.stateful.set_local(
        StatefulLocal(
            stateful_firewall_settings=StatefulFirewallSettings(udp_flow_timeout=5),
            stateful_firewall_settings_v6=StatefulFirewallSettings(),
            stateful_firewall_enabled=False,
        )
    )
    local = edge.data.stateful_firewall_settings.local
    assert isinstance(local, StatefulFirewallSettings)
    assert local.udp_flow_timeout == 5


def rule(name: str, **extra: Any) -> FirewallOutboundRule:
    return FirewallOutboundRule.model_validate(
        {"name": name, "action": {"allow_or_deny": "deny"}, "match": {}, **extra}
    )


def test_edge_rules_add_delete_reorder_keep_profile_rules_out() -> None:
    profile_rule = {"name": "p1", "match": {}, "action": {"allow_or_deny": "allow"}}
    edge_rules = [
        {"name": "a", "match": {}, "action": {"allow_or_deny": "deny"}},
        {"name": "b", "match": {}, "action": {"allow_or_deny": "deny"}},
        {"name": "c", "match": {}, "action": {"allow_or_deny": "deny"}},
    ]
    segment = {"segment": desc(0, "Global Segment")}
    edge, profile = bound(
        {"segments": [{**segment, "outbound": copy.deepcopy(edge_rules)}]},
        {"segments": [{**segment, "outbound": [copy.deepcopy(profile_rule)]}]},
    )
    outbound = edge.data.segments[0].outbound
    del outbound[1]  # b
    outbound.append(rule("d"))
    outbound.reverse()  # d, c, a
    names = [r["name"] for r in payload(edge, profile)["segments"][0]["outbound"]]
    assert names == ["d", "c", "a"]
    assert "p1" not in names


def test_localize_starts_from_the_profile() -> None:
    profile_services = {"loggingEnabled": True, "ssh": {"enabled": True}}
    edge, profile = bound({}, {"services": profile_services})
    services = edge.data.services.localize()
    services.post = ConsoleService(enabled=True)
    data = payload(edge, profile)
    assert data["services"] == {**profile_services, "post": {"enabled": True}}
    assert isinstance(profile.raw_data, dict)
    assert profile.raw_data["services"] == profile_services  # the profile is unchanged


def test_a_value_made_local_carries_none_of_the_profiles_server_assigned_ids() -> None:
    profile_services = {
        "ssh": {"enabled": True},
        "console": {"enabled": False, "ruleLogicalId": "xyz"},
    }
    edge, profile = bound({}, {"services": profile_services})
    _ = edge.data.services.localize()
    services = payload(edge, profile)["services"]
    assert services == {"ssh": {"enabled": True}, "console": {"enabled": False}}
    assert "ruleLogicalId" not in services["ssh"]
    assert "ruleLogicalId" not in services["console"]
    # The profile keeps its own.
    assert isinstance(profile.raw_data, dict)
    assert profile.raw_data["services"]["console"]["ruleLogicalId"] == "xyz"
    assert profile.data.services.console.rule_logical_id == "xyz"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


def test_a_patch_into_an_inherited_section_carries_none_of_the_profiles_ids() -> None:
    profile_services = {"console": {"enabled": False, "ruleLogicalId": "xyz"}}
    edge, profile = bound({}, {"services": profile_services})
    _ = edge.data.merge_patch({"services": {"ssh": {"enabled": True}}})
    services = payload(edge, profile)["services"]
    assert services["console"] == {"enabled": False}
    assert services["ssh"] == {"enabled": True}


def test_a_stored_null_id_is_kept_as_read() -> None:
    # A services object the edge stores with a null ID round-trips unchanged,
    # as does a profile's.
    raw = {"ssh": {"enabled": True, "ruleLogicalId": None}}
    edge, profile = bound({"services": raw}, {"services": raw})
    edge.data.syslog_forwarding.set_local(True)
    assert payload(edge, profile) == {
        "segments": [],
        "services": raw,
        "syslog_forwarding": True,
    }
    assert plan_module(profile, level=ConfigContext.PROFILE).operation == "none"


def test_a_rule_match_keeps_the_vco_s_vlan_type_key_for_key() -> None:
    stored = {"vlan_type": "local", "svlan": 3}
    match = OutboundRuleMatch.model_validate(stored)
    assert match.vlan_type == "local"
    written = match.model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert written == stored


def test_a_new_outbound_rule_is_written_with_vlan_type_and_not_vlanType() -> None:  # noqa: N802 (the VCO key is the point of the test)
    edge, profile = bound({"segments": [{"segment": desc(0, "Global Segment")}]})
    edge.data.segments[0].outbound.append(rule("new"))
    (written,) = payload(edge, profile)["segments"][0]["outbound"]
    assert written["match"]["vlan_type"] == "end-to-end"
    assert "vlanType" not in written["match"]


def test_a_rule_keeps_its_logging_flags_key_for_key() -> None:
    base = {"name": "r", "action": {"allow_or_deny": "deny"}, "match": {}}
    flagged = {**base, "hostedLoggingEnabled": True, "syslogForwardingEnabled": False}
    for stored in (flagged, base):
        written = FirewallOutboundRule.model_validate(stored).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )
        assert written == stored
    loaded = FirewallOutboundRule.model_validate(flagged)
    assert loaded.hosted_logging_enabled is True
    assert loaded.syslog_forwarding_enabled is False
    assert FirewallOutboundRule.model_validate(base).hosted_logging_enabled is MISSING
