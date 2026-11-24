"""The firewall module's kinds, groups and display defaults as declared, and where they come from."""

from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall import (
    AtpLocal,
    FirewallSegment,
    NetworkProtectionLocal,
    StatefulLocal,
)
from pyvelo.client.models.configuration.profile_segments import module_template_value
from pyvelo.core import (
    ByPresence,
    Composed,
    Default,
    Edge,
    Kind,
    MemberOf,
    Profile,
    field_kind,
)
from pyvelo.core.effective import Authority
from pyvelo.core.owned import groups_of

# name -> (kind, member of, wire key)
INHERITABLE = Kind.INHERITABLE
UNCONVERTED = Kind.UNCONVERTED
STRUCTURAL = Kind.STRUCTURAL

MODULE_TABLE: dict[str, tuple[Kind, str | None]] = {
    "firewall_enabled": (INHERITABLE, None),
    "inbound": (UNCONVERTED, None),
    "inbound_v6": (UNCONVERTED, None),
    "segments": (STRUCTURAL, None),
    "syslog_forwarding": (INHERITABLE, None),
    "firewall_logging_enabled": (INHERITABLE, None),
    "atp_enabled": (INHERITABLE, "atp"),
    "stateful_firewall_enabled": (INHERITABLE, "stateful"),
    "security_features": (INHERITABLE, "atp"),
    "services": (INHERITABLE, None),
    "network_protection_settings": (INHERITABLE, "network_protection"),
    "network_protection_settings_v6": (INHERITABLE, "network_protection"),
    "stateful_firewall_settings": (INHERITABLE, "stateful"),
    "stateful_firewall_settings_v6": (INHERITABLE, "stateful"),
}
SEGMENT_TABLE: dict[str, Kind] = {
    "segment": STRUCTURAL,
    "firewall_logging_enabled": UNCONVERTED,
    "outbound": Kind.ADDITIVE,
}


def group_of(name: str, fields: Any) -> str | None:
    return next(
        (m.group for m in field_kind(fields[name]).metadata if isinstance(m, MemberOf)),
        None,
    )


def test_the_firewall_kinds_are_as_declared():
    fields = FirewallData[Edge].model_fields
    assert set(fields) == set(MODULE_TABLE)
    for name, (kind, group) in MODULE_TABLE.items():
        found = field_kind(fields[name])
        assert found.kind is kind, name
        assert group_of(name, fields) == group, name
        if kind is INHERITABLE:
            assert isinstance(found.encoding, ByPresence), name
            assert sum(isinstance(m, Default) for m in found.metadata) == 1, name
    # The unconverted fields are exactly these.
    assert {n for n, f in fields.items() if field_kind(f).kind is UNCONVERTED} == {
        "inbound",
        "inbound_v6",
    }
    segment = FirewallSegment[Edge].model_fields
    assert {n: field_kind(f).kind for n, f in segment.items()} == SEGMENT_TABLE
    assert {n for n, f in segment.items() if field_kind(f).kind is UNCONVERTED} == {
        "firewall_logging_enabled"
    }
    # The same on the profile layer: one declaration.
    assert {
        n: field_kind(f).kind for n, f in FirewallData[Profile].model_fields.items()
    } == {n: k for n, (k, _) in MODULE_TABLE.items()}


def test_the_firewall_groups_are_declared_with_their_anchors_and_bundles():
    groups = groups_of(FirewallData[Edge])
    assert {n: (g.anchor, g.bundle) for n, g in groups.items()} == {
        "stateful": ("stateful_firewall_settings", StatefulLocal),
        "network_protection": ("network_protection_settings", NetworkProtectionLocal),
        "atp": ("atp_enabled", AtpLocal),
    }
    assert {n: g.members(FirewallData[Edge]) for n, g in groups.items()} == {
        "stateful": (
            "stateful_firewall_enabled",
            "stateful_firewall_settings",
            "stateful_firewall_settings_v6",
        ),
        "network_protection": (
            "network_protection_settings",
            "network_protection_settings_v6",
        ),
        "atp": ("atp_enabled", "security_features"),
    }


def test_firewall_enabled_has_the_profile_false_wins_authority():
    metadata = field_kind(FirewallData[Edge].model_fields["firewall_enabled"]).metadata
    (authority,) = [m for m in metadata if isinstance(m, Authority)]
    assert authority.rule(False) is True
    assert authority.rule(True) is False


def test_the_display_defaults_are_the_templates():
    profile = FirewallData[Profile].model_validate({"segments": []})
    assert profile.stateful_firewall_enabled is False
    assert profile.firewall_logging_enabled is False
    assert profile.firewall_enabled is True
    assert profile.syslog_forwarding is False
    assert profile.atp_enabled is False
    assert profile.network_protection_settings.icmp_based_attacks.enable_ping_of_death
    assert (
        profile.network_protection_settings_v6.icmp_based_attacks.enable_ping_of_death
    )
    assert profile.stateful_firewall_settings.udp_flow_timeout == 300
    assert profile.stateful_firewall_settings_v6.established_tcp_flow_timeout == 7440
    assert profile.services.ssh.enabled is False  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    assert profile.security_features.idps_enabled is False
    # Each one is what the template file holds, and none is written.
    for key in ("firewall_enabled", "syslog_forwarding", "stateful_firewall_enabled"):
        assert getattr(profile, key) is module_template_value("firewall", key)
    assert profile.model_dump(mode="json", by_alias=True) == {
        "segments": [],
        "inbound": [],
        "inboundV6": [],
    }
    assert profile.model_fields_set == {"segments"}


def test_each_default_equals_the_template_value():
    profile = FirewallData[Profile].model_validate({"segments": []})
    for attribute, key in (
        ("services", "services"),
        ("stateful_firewall_settings", "statefulFirewallSettings"),
        ("stateful_firewall_settings_v6", "statefulFirewallSettingsV6"),
        ("network_protection_settings", "networkProtectionSettings"),
        ("network_protection_settings_v6", "networkProtectionSettingsV6"),
        ("security_features", "securityFeatures"),
    ):
        value = getattr(profile, attribute)
        dumped = value.model_dump(mode="json", by_alias=True)
        assert dumped == module_template_value("firewall", key)


def test_a_display_default_is_a_fresh_object():
    one = FirewallData[Profile].model_validate({"segments": []})
    two = FirewallData[Profile].model_validate({"segments": []})
    assert one.services is not two.services
    one.stateful_firewall_settings.udp_flow_timeout = 1
    assert two.stateful_firewall_settings.udp_flow_timeout == 300


def test_an_edge_that_stores_nothing_holds_nothing():
    edge = FirewallData[Edge].model_validate({"segments": []})
    for name in MODULE_TABLE:
        if MODULE_TABLE[name][0] is INHERITABLE:
            owned = getattr(edge, name)
            assert owned.stored is MISSING
            assert owned.is_local is False


def test_module_template_value_raises_for_an_unknown_key():
    with pytest.raises(KeyError):
        module_template_value("firewall", "noSuchKey")
    with pytest.raises(ValueError, match="nope"):
        module_template_value("nope", "services")


def test_module_template_value_is_a_copy():
    first = module_template_value("firewall", "statefulFirewallSettings")
    assert isinstance(first, dict)
    first["udpFlowTimeout"] = 1
    assert module_template_value("firewall", "statefulFirewallSettings") == {
        "establishedTcpFlowTimeout": 7440,
        "nonEstablishedTcpFlowTimeout": 240,
        "udpFlowTimeout": 300,
        "otherFlowTimeout": 60,
    }


@pytest.mark.parametrize("layer", [Profile, Composed])
def test_a_constructed_model_reads_a_missing_inheritable_as_its_display_default(layer):
    built = FirewallData[layer].model_construct()
    parsed = FirewallData[layer].model_validate({})
    assert built.atp_enabled is False
    assert built.services.ssh.enabled is False  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    assert built.model_fields_set == set()
    # Nothing was given, so nothing is written, as for a parse.
    assert built.model_dump(mode="json", by_alias=True) == parsed.model_dump(
        mode="json", by_alias=True
    )
    given = FirewallData[layer].model_construct(atp_enabled=True)
    assert given.model_fields_set == {"atp_enabled"}
    assert given.atp_enabled is True
