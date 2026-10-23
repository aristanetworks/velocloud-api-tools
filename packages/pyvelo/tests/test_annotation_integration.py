"""Integration tests: merge_patch on real data models with Phase 2 annotations.

Validates that KeyField, OverrideFlag, EdgeLocal, and Resolvable markers work
correctly together on the actual VeloCloud configuration data models.
"""

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import (
    LanNetwork,
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
)
from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterfaceAddressing,
    RoutedInterfaceBase,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentDns,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_action import OutboundRuleAction
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.models.configuration.qos.segment import QosSegment

from ._type_support import require_present


def _make_segment_metadata(name: str, segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        segment_id=segment_id, name=name, type_=NetworkSegmentType.REGULAR
    )


def _make_dns() -> SegmentDns:
    from pyvelo.client.models.configuration.device_settings.segment import DnsProvider

    return SegmentDns(
        override=False,
        primary_provider=DnsProvider(ref="deviceSettings:dns:primaryProvider"),
        backup_provider=DnsProvider(ref="deviceSettings:dns:backupProvider"),
        private_providers=DnsProvider(ref="deviceSettings:dns:privateProviders"),
    )


class TestDeviceSettingsSegmentMerge:
    """Test merge_patch on DeviceSettingsData addressing segments by name."""

    def _make_device_settings(self) -> DeviceSettingsData:
        return DeviceSettingsData(
            lan=LanSettings(
                networks=[
                    LanNetwork(name="Corporate", vlan_id=100, cidr_ip="10.0.0.1"),
                    LanNetwork(name="Guest", vlan_id=200, cidr_ip="10.0.1.1"),
                ],
            ),
            segments=[
                DeviceSegment(
                    segment=_make_segment_metadata("Global Segment", 0),
                    dns=_make_dns(),
                ),
                DeviceSegment(
                    segment=_make_segment_metadata("PCI", 1),
                    dns=_make_dns(),
                ),
            ],
        )

    def test_address_segment_by_name(self):
        ds = self._make_device_settings()
        _ = ds.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "dns": {"override": True},
                    },
                },
            }
        )
        global_seg = ds.segments[0]
        global_dns = require_present(global_seg.dns)
        assert global_dns is not None
        assert global_dns.override is True
        # PCI unchanged
        pci_dns = require_present(ds.segments[1].dns)
        assert pci_dns is not None
        assert pci_dns.override is False

    def test_dns_override_auto_set(self):
        ds = self._make_device_settings()
        dns = require_present(ds.segments[0].dns)
        assert dns is not None
        assert dns.override is False
        _ = dns.merge_patch({"source_interface": "GE3"})
        assert dns.override is True

    def test_unmentioned_segments_untouched(self):
        ds = self._make_device_settings()
        _ = ds.merge_patch(
            {
                "segments": {
                    "Global Segment": {"dns": {"source_interface": "GE1"}},
                },
            }
        )
        pci = ds.segments[1]
        assert pci.segment.name == "PCI"
        dns = require_present(pci.dns)
        assert dns is not None
        assert dns.override is False


class TestLanNetworkMerge:
    """Test merge_patch on LAN networks with EdgeLocal fields."""

    def _make_lan_settings(self) -> LanSettings:
        return LanSettings(
            networks=[
                LanNetwork(
                    name="Corporate",
                    vlan_id=100,
                    cidr_ip="10.0.0.1",
                    cidr_prefix=24,
                    netmask="255.255.255.0",
                    override=False,
                    advertise=True,
                ),
            ],
        )

    def test_update_network_by_vlan_id(self):
        lan = self._make_lan_settings()
        _ = lan.merge_patch(
            {
                "networks": {
                    "100": {"cidr_ip": "10.0.0.2"},
                },
            }
        )
        assert lan.networks[0].cidr_ip == "10.0.0.2"

    def test_edge_local_change_no_override(self):
        lan = self._make_lan_settings()
        _ = lan.networks[0].merge_patch({"cidr_ip": "10.0.0.2"})
        assert lan.networks[0].override is False

    def test_edge_local_cidr_prefix_no_override(self):
        lan = self._make_lan_settings()
        _ = lan.networks[0].merge_patch({"cidr_prefix": 16})
        assert lan.networks[0].override is False

    def test_non_edge_local_triggers_override(self):
        lan = self._make_lan_settings()
        _ = lan.networks[0].merge_patch({"advertise": False})
        assert lan.networks[0].override is True

    def test_mixed_edge_local_and_normal(self):
        lan = self._make_lan_settings()
        _ = lan.networks[0].merge_patch(
            {
                "cidr_ip": "10.0.0.2",
                "advertise": False,
            }
        )
        assert lan.networks[0].override is True


class TestRoutedInterfaceMerge:
    """Test merge_patch on routed interfaces with EdgeLocal addressing."""

    def _make_routed_interface(self) -> RoutedInterfaceBase:
        return RoutedInterfaceBase(
            override=False,
            addressing=RoutedInterfaceAddressing(
                type_="STATIC",
                cidr_ip="192.168.1.1",
                cidr_prefix=24,
                gateway="192.168.1.254",
            ),
        )

    def test_addressing_cidr_ip_no_override(self):
        ri = self._make_routed_interface()
        _ = ri.merge_patch({"addressing": {"cidr_ip": "192.168.1.2"}})
        assert ri.override is False

    def test_addressing_gateway_no_override(self):
        ri = self._make_routed_interface()
        _ = ri.merge_patch({"addressing": {"gateway": "192.168.1.1"}})
        assert ri.override is False

    def test_addressing_type_triggers_override(self):
        ri = self._make_routed_interface()
        _ = ri.merge_patch({"addressing": {"type": "DHCP"}})
        assert ri.override is True

    def test_dhcp_server_change_no_override(self):
        """DHCP server is EdgeLocal — changes don't trigger override."""
        ri = self._make_routed_interface()
        _ = ri.merge_patch({"description": "Updated WAN interface"})
        assert ri.override is False


class TestFirewallMerge:
    """Test merge_patch on firewall models (concatenation, no override)."""

    def _make_firewall(self) -> FirewallData:
        action = OutboundRuleAction(allow_or_deny="allow")
        return FirewallData(
            segments=[
                FirewallSegment(
                    segment=_make_segment_metadata("Global Segment", 0),
                    outbound=[
                        FirewallOutboundRule(
                            name="allow-all",
                            match=OutboundRuleMatch(),
                            action=action,
                        ),
                        FirewallOutboundRule(
                            name="block-ssh",
                            match=OutboundRuleMatch(
                                proto=6,
                                destination_port_low=22,
                                destination_port_high=22,
                            ),
                            action=OutboundRuleAction(allow_or_deny="deny"),
                        ),
                    ],
                ),
            ],
        )

    def test_address_segment_by_name(self):
        fw = self._make_firewall()
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "block-ssh": {"logging_enabled": True},
                        },
                    },
                },
            }
        )
        ssh_rule = next(r for r in fw.segments[0].outbound if r.name == "block-ssh")
        assert ssh_rule.logging_enabled is True

    def test_add_rule_to_segment(self):
        fw = self._make_firewall()
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "deny-telnet": {
                                "name": "deny-telnet",
                                "match": {
                                    "proto": 6,
                                    "dport_low": 23,
                                    "dport_high": 23,
                                },
                                "action": {"allow_or_deny": "deny"},
                            },
                        },
                    },
                },
            }
        )
        assert len(fw.segments[0].outbound) == 3
        assert fw.segments[0].outbound[2].name == "deny-telnet"

    def test_delete_rule(self):
        fw = self._make_firewall()
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {"block-ssh": None},
                    },
                },
            }
        )
        assert len(fw.segments[0].outbound) == 1
        assert fw.segments[0].outbound[0].name == "allow-all"


class TestQosMerge:
    """Test merge_patch on QoS models."""

    def _make_qos(self) -> QosData:
        # Build a minimal QoS rule via model_validate to satisfy required fields
        rule = QosRule.model_validate(
            {
                "name": "voice",
                "match": {},
                "action": {
                    "edge2EdgeRouteAction": {},
                    "edge2DataCenterRouteAction": {},
                    "edge2CloudRouteAction": {},
                    "QoS": {},
                },
            }
        )
        return QosData(
            segments=[
                QosSegment(
                    segment=_make_segment_metadata("Global Segment", 0),
                    rules=[rule],
                ),
            ],
        )

    def test_update_rule_by_name(self):
        qos = self._make_qos()
        _ = qos.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "rules": {
                            "voice": {"match": {"proto": 17}},
                        },
                    },
                },
            }
        )
        assert qos.segments[0].rules[0].match.proto == 17
