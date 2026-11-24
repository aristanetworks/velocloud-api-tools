"""The firewall's ownership scenarios, each through ``plan_module`` on edge modules bound to a profile."""

import copy
from typing import Any

from pydantic import MISSING
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall import (
    ConsoleService,
    FirewallOutboundRule,
    StatefulFirewallSettings,
    StatefulLocal,
)
from pyvelo.client.module_write import plan_module
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


def bound(edge_raw: dict[str, Any], profile_raw: dict[str, Any] | None = None):
    """An edge module of *edge_raw* bound to a profile module of *profile_raw*, and that one."""
    edge = fw({"segments": [], **edge_raw}, layer=Edge)
    profile = fw({"segments": [], **(profile_raw or {})}, layer=Profile, module_id=2)
    edge.data.bind_profile(profile.raw_data)
    return edge, profile


def without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    return value


def payload(edge: Any, profile: Any) -> dict[str, Any]:
    return plan_module(edge, level=EDGE, profile=profile).data


def test_an_unrelated_edit_leaves_an_inherited_section_absent():
    edge, profile = bound({})
    assert edge.data.services.stored is MISSING
    edge.data.syslog_forwarding.set_local(True)
    data = payload(edge, profile)
    assert data == {"segments": [], "syslog_forwarding": True}
    assert "services" not in data


async def test_an_explicit_local_false_survives_a_reload(
    mock_vco_client: MockVcoClientFactory,
):
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


def test_a_stored_null_is_kept_and_does_not_count_as_local():
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


def test_inheriting_the_stateful_group_removes_exactly_its_three_keys():
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


def test_setting_stateful_enabled_false_while_local_keeps_the_settings():
    edge, profile = bound(STATEFUL, PROFILE_STATEFUL)
    edge.data.stateful_firewall_enabled.set_local(False)
    plan = plan_module(edge, level=EDGE, profile=profile)
    assert plan.data == {**STATEFUL, "segments": [], "stateful_firewall_enabled": False}
    assert [str(c.path) for c in plan.changes] == ["stateful_firewall_enabled"]
    assert edge.data.stateful.is_local


def test_setting_one_stateful_member_while_inherited_refuses():
    import pytest
    from pyvelo.client.exceptions import GroupOwnershipError

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


def test_edge_rules_add_delete_reorder_keep_profile_rules_out():
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


def test_localize_starts_from_the_profile():
    profile_services = {"loggingEnabled": True, "ssh": {"enabled": True}}
    edge, profile = bound({}, {"services": profile_services})
    services = edge.data.services.localize()
    services.post = ConsoleService(enabled=True)
    data = payload(edge, profile)
    # ruleLogicalId is the server's: a model that has none dumps it as null.
    assert without_nulls(data["services"]) == {
        **profile_services,
        "post": {"enabled": True},
    }
    assert isinstance(profile.raw_data, dict)
    assert profile.raw_data["services"] == profile_services  # the profile is unchanged
