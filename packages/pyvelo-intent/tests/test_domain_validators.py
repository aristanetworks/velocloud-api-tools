"""Tests for domain-specific validators: overlapping subnets, rule uniqueness, interface consistency."""

from datetime import UTC, datetime
from typing import TypedDict

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
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.config.validators.device_settings import (
    validate_no_overlapping_subnets,
)
from pyvelo_intent.config.validators.firewall import (
    validate_interface_firewall_consistency,
    validate_rule_names_unique,
)


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


def _make_state(modules: list[AnyConfigurationModule]) -> ConfigState:
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(
            name="Test Profile", id_=1, modules=modules
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


class TestOverlappingSubnets:
    def test_non_overlapping_passes(self):
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

    def test_overlapping_detected(self):
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

    def test_different_segments_no_overlap(self):
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

    def test_no_device_settings_module(self):
        """No errors when deviceSettings module is absent."""
        state = _make_state([])
        result = validate_no_overlapping_subnets.validate(state)
        assert result.is_valid


class TestRuleNamesUnique:
    def test_unique_rule_names_pass(self):
        """Unique rule names should pass."""
        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(
                segments=[
                    FirewallSegment(
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

    def test_duplicate_firewall_rule_names(self):
        """Duplicate firewall rule names should be flagged."""
        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(
                segments=[
                    FirewallSegment(
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

    def test_no_modules(self):
        """No errors when modules are absent."""
        state = _make_state([])
        result = validate_rule_names_unique.validate(state)
        assert result.is_valid


class TestInterfaceFirewallConsistency:
    def test_no_errors_without_both_modules(self):
        """No errors when not both modules present."""
        state = _make_state([])
        result = validate_interface_firewall_consistency.validate(state)
        assert result.is_valid
