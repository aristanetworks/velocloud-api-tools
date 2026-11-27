"""Tests for domain-specific validators.

They check overlapping subnets, rule uniqueness and interface consistency.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import (
    LanNetwork,
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import DeviceSegment
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.config.validators.device_settings import (
    validate_no_overlapping_subnets,
)
from pyvelo.config.validators.firewall import (
    validate_interface_firewall_consistency,
    validate_rule_names_unique,
)
from pyvelo.core.layers import Profile
from pyvelo.core.rebase import Key
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from shown_paths import reached, round_trips


class _ModuleKwargs(TypedDict):
    created: datetime
    modified: datetime
    type_: ConfigurationModuleType
    description: str | None
    configuration_id: int
    enterprise_logical_id: None
    schema_version: None
    version: None
    metadata: None


def _seg_meta(name: str = "Global Segment", segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
    )


def _mod_kwargs() -> _ModuleKwargs:
    return _ModuleKwargs(
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(modules: list[AnyConfigurationModule[Profile]]) -> ConfigState:
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(
            name="Test Profile", id_=1, modules=modules
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


class TestOverlappingSubnets:
    def test_non_overlapping_passes(self) -> None:
        """Non-overlapping subnets in same segment should pass."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(
                    networks=[
                        LanNetwork(
                            name="Engineering",
                            vlan_id=100,
                            cidr_ip="10.0.1.0",
                            cidr_prefix=24,
                            segment_id=0,
                        ),
                        LanNetwork(
                            name="Sales",
                            vlan_id=200,
                            cidr_ip="10.0.2.0",
                            cidr_prefix=24,
                            segment_id=0,
                        ),
                    ]
                ),
                segments=[DeviceSegment(segment=_seg_meta())],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = validate_no_overlapping_subnets.validate(state)
        assert result.is_valid

    def test_overlapping_detected(self) -> None:
        """Overlapping subnets in same segment should be flagged."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(
                    networks=[
                        LanNetwork(
                            name="Net-A",
                            vlan_id=100,
                            cidr_ip="10.0.1.0",
                            cidr_prefix=24,
                            segment_id=0,
                        ),
                        LanNetwork(
                            name="Net-B",
                            vlan_id=200,
                            cidr_ip="10.0.1.128",
                            cidr_prefix=25,
                            segment_id=0,
                        ),
                    ]
                ),
                segments=[DeviceSegment(segment=_seg_meta())],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = validate_no_overlapping_subnets.validate(state)
        assert not result.is_valid
        assert any("overlaps" in e.message for e in result.errors)

    def test_different_segments_no_overlap(self) -> None:
        """Same subnet in different segments should not be flagged."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(
                    networks=[
                        LanNetwork(
                            name="Net-Seg0",
                            vlan_id=100,
                            cidr_ip="10.0.1.0",
                            cidr_prefix=24,
                            segment_id=0,
                        ),
                        LanNetwork(
                            name="Net-Seg1",
                            vlan_id=200,
                            cidr_ip="10.0.1.0",
                            cidr_prefix=24,
                            segment_id=1,
                        ),
                    ]
                ),
                segments=[
                    DeviceSegment(segment=_seg_meta("Global Segment", 0)),
                    DeviceSegment(segment=_seg_meta("PCI", 1)),
                ],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = validate_no_overlapping_subnets.validate(state)
        assert result.is_valid

    def test_no_device_settings_module(self) -> None:
        """No errors when deviceSettings module is absent."""
        state = _make_state([])
        result = validate_no_overlapping_subnets.validate(state)
        assert result.is_valid


class TestRuleNamesUnique:
    def test_unique_rule_names_pass(self) -> None:
        """Unique rule names should pass."""
        fw_mod = FirewallConfigurationModule[Profile](
            name="firewall",
            data=FirewallData[Profile](
                segments=[
                    FirewallSegment[Profile](
                        segment=_seg_meta(),
                        outbound=[
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "rule-1",
                                    "match": {},
                                    "action": {"allow_or_deny": "allow"},
                                }
                            ),
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "rule-2",
                                    "match": {},
                                    "action": {"allow_or_deny": "deny"},
                                }
                            ),
                        ],
                    )
                ]
            ),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        result = validate_rule_names_unique.validate(state)
        assert result.is_valid

    def test_duplicate_firewall_rule_names(self) -> None:
        """Duplicate firewall rule names should be flagged."""
        fw_mod = FirewallConfigurationModule[Profile](
            name="firewall",
            data=FirewallData[Profile](
                segments=[
                    FirewallSegment[Profile](
                        segment=_seg_meta(),
                        outbound=[
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "same",
                                    "match": {},
                                    "action": {"allow_or_deny": "allow"},
                                }
                            ),
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "same",
                                    "match": {},
                                    "action": {"allow_or_deny": "deny"},
                                }
                            ),
                        ],
                    )
                ]
            ),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        result = validate_rule_names_unique.validate(state)
        assert not result.is_valid
        assert any("Duplicate rule name" in e.message for e in result.errors)

    def test_no_modules(self) -> None:
        """No errors when modules are absent."""
        state = _make_state([])
        result = validate_rule_names_unique.validate(state)
        assert result.is_valid


class TestInterfaceFirewallConsistency:
    def test_no_errors_without_both_modules(self) -> None:
        """No errors when not both modules present."""
        state = _make_state([])
        result = validate_interface_firewall_consistency.validate(state)
        assert result.is_valid


def _qos_rule(name: str) -> dict[str, Any]:
    """A QoS rule called *name*, with a logical ID of its own."""
    return {
        "name": name,
        "match": {},
        "action": {
            "edge2EdgeRouteAction": {},
            "edge2DataCenterRouteAction": {},
            "edge2CloudRouteAction": {},
            "QoS": {},
        },
        "ruleLogicalId": str(uuid.uuid4()),
    }


def state_with_two_rules_named(name: str) -> ConfigState:
    """A state whose QoS module has two rules called *name* in its Guest segment."""
    qos_mod = QosConfigurationModule[Profile](
        data=QosData[Profile].model_validate(
            {
                "segments": [
                    qos_entry(GLOBAL),
                    qos_entry(GUEST, rules=[_qos_rule(name), _qos_rule(name)]),
                ]
            }
        ),
        id_=3,
        **_mod_kwargs(),
    )
    return _make_state([qos_mod])


def test_a_duplicate_rule_name_is_at_the_second_rule_of_its_module() -> None:
    state = state_with_two_rules_named("Web")
    (finding,) = validate_rule_names_unique.validate(state).errors
    assert finding.module == "business_policy"
    assert finding.path.parts[:2] == ("segments", Key("Guest"))
    data = state.profile_config.qos.data
    (guest,) = [s for s in data.segments if s.segment.name == "Guest"]
    assert reached(data, finding.path) is guest.rules[1]
    round_trips(finding.path)


def _outbound_rule(name: str, source_interface: str = "") -> FirewallOutboundRule:
    return FirewallOutboundRule.model_validate(
        {
            "name": name,
            "match": {"sInterface": source_interface},
            "action": {"allow_or_deny": "allow"},
        }
    )


def _firewall_module(
    *rules: FirewallOutboundRule,
) -> FirewallConfigurationModule[Profile]:
    """A firewall module whose Global Segment has *rules*, after a Guest segment."""
    data = FirewallData[Profile].model_validate(
        {"segments": [firewall_entry(GUEST), firewall_entry(GLOBAL)]}
    )
    data.segments[1].outbound = list(rules)
    return FirewallConfigurationModule[Profile](data=data, id_=1, **_mod_kwargs())


def test_a_duplicate_firewall_rule_name_is_at_the_second_rule_of_its_module() -> None:
    state = _make_state(
        [_firewall_module(_outbound_rule("same"), _outbound_rule("same"))]
    )
    (finding,) = validate_rule_names_unique.validate(state).errors
    assert finding.module == "firewall"
    assert finding.path.parts[:2] == ("segments", Key("Global Segment"))
    data = state.profile_config.firewall.data
    assert reached(data, finding.path) is data.segments[1].outbound[1]
    round_trips(finding.path)


def _networks_state(*segment_ids: int) -> ConfigState:
    """A state with an overlapping network in each segment ID of *segment_ids*.

    Two networks each, of one subnet, in device settings with a Global Segment.
    """
    networks = [
        LanNetwork(
            name=f"Net-{segment_id}-{n}",
            vlan_id=100 + n,
            cidr_ip="10.0.1.0",
            cidr_prefix=24,
            segment_id=segment_id,
        )
        for segment_id in segment_ids
        for n in range(2)
    ]
    data = DeviceSettingsData.model_validate(
        {"lan": {"networks": []}, "segments": [ds_entry(GLOBAL)]}
    )
    data.lan.networks = networks
    return _make_state(
        [DeviceSettingsConfigurationModule(data=data, id_=2, **_mod_kwargs())]
    )


def test_an_overlap_is_at_the_networks_of_its_module_and_names_the_segment() -> None:
    state = _networks_state(0, 7)
    findings = validate_no_overlapping_subnets.validate(state).errors
    data = state.profile_config.device_settings.data
    for finding in findings:
        assert finding.module == "device_settings"
        assert reached(data, finding.path) is data.lan.networks
        round_trips(finding.path)
    assert [f.message.rsplit(" in ", 1)[1] for f in findings] == [
        "segment 'Global Segment'",
        "segment 7",
    ]


def test_an_unknown_interface_is_at_the_match_of_its_rule() -> None:
    ds = DeviceSettingsData.model_validate(
        {
            "lan": {"networks": []},
            "segments": [ds_entry(GLOBAL)],
            "routedInterfaces": [{"name": "GE1"}],
        }
    )
    state = _make_state(
        [
            DeviceSettingsConfigurationModule(data=ds, id_=2, **_mod_kwargs()),
            _firewall_module(
                _outbound_rule("web", "GE1"), _outbound_rule("ssh", "GE9")
            ),
        ]
    )
    result = validate_interface_firewall_consistency.validate(state)
    assert result.errors == []
    (finding,) = result.warnings
    assert finding.module == "firewall"
    data = state.profile_config.firewall.data
    assert reached(data, finding.path) is data.segments[1].outbound[1].match
    round_trips(finding.path)
    assert finding.message == (
        "Rule references source interface 'GE9' not found in device_settings"
    )
