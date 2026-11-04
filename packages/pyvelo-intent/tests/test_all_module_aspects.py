"""Tests for aspects across all modules (QOS, WAN, control plane, device settings refinements)."""

from pyvelo_intent.config.aspect import AspectRegistry
from pyvelo_intent.config.aspects import register_default_aspects
from pyvelo_intent.config.aspects.control_plane import CONTROL_PLANE_ASPECTS
from pyvelo_intent.config.aspects.device_settings import DEVICE_SETTINGS_ASPECTS
from pyvelo_intent.config.aspects.firewall import FIREWALL_ASPECTS
from pyvelo_intent.config.aspects.qos import QOS_ASPECTS
from pyvelo_intent.config.aspects.wan import WAN_ASPECTS


class TestAllAspectRegistration:
    def test_all_aspects_register(self):
        """All predefined aspects should register without conflict."""
        registry = AspectRegistry()
        register_default_aspects(registry)

        summaries = registry.list_aspects()
        names = {s.name for s in summaries}

        # Verify specific expected aspects exist
        assert "firewall_rules" in names
        assert "dns" in names
        assert "vlans" in names
        assert "interfaces" in names
        assert "business_policy" in names
        assert "wan_links" in names
        assert "wan_networks" in names
        assert "bgp" in names
        assert "ospf" in names
        assert "gateway_selection" in names

    def test_aspect_counts(self):
        """Verify all aspect lists have expected counts."""
        assert len(FIREWALL_ASPECTS) >= 1
        assert (
            len(DEVICE_SETTINGS_ASPECTS) >= 5
        )  # dns, vlans, interfaces, ntp, syslog + new ones
        assert len(QOS_ASPECTS) >= 1
        assert len(WAN_ASPECTS) >= 2
        assert len(CONTROL_PLANE_ASPECTS) >= 2


class TestQosAspects:
    def test_business_policy_is_segment_scoped(self):
        """Business policy aspect should be segment-scoped."""
        aspect = QOS_ASPECTS[0]
        assert aspect.name == "business_policy"
        assert aspect.module == "QOS"
        assert aspect.segment_scoped is True

    def test_business_policy_has_examples(self):
        """Business policy aspect should have examples."""
        aspect = QOS_ASPECTS[0]
        assert aspect.examples is not None
        assert len(aspect.examples) > 0


class TestWanAspects:
    def test_wan_links_not_segment_scoped(self):
        """WAN links are not segment-scoped."""
        links = next(a for a in WAN_ASPECTS if a.name == "wan_links")
        assert links.segment_scoped is False
        assert links.module == "WAN"

    def test_wan_networks_not_segment_scoped(self):
        """WAN networks are not segment-scoped."""
        nets = next(a for a in WAN_ASPECTS if a.name == "wan_networks")
        assert nets.segment_scoped is False
        assert nets.module == "WAN"


class TestControlPlaneAspects:
    def test_bgp_is_segment_scoped(self):
        """BGP aspect should be segment-scoped (under deviceSettings)."""
        bgp = next(a for a in CONTROL_PLANE_ASPECTS if a.name == "bgp")
        assert bgp.segment_scoped is True
        assert bgp.module == "deviceSettings"

    def test_ospf_is_segment_scoped(self):
        """OSPF aspect should be segment-scoped (under deviceSettings)."""
        ospf = next(a for a in CONTROL_PLANE_ASPECTS if a.name == "ospf")
        assert ospf.segment_scoped is True
        assert ospf.module == "deviceSettings"

    def test_gateway_selection_not_segment_scoped(self):
        """Gateway selection should not be segment-scoped."""
        gw = next(a for a in CONTROL_PLANE_ASPECTS if a.name == "gateway_selection")
        assert gw.segment_scoped is False
        assert gw.module == "controlPlane"


class TestDeviceSettingsRefinedAspects:
    def test_new_aspects_present(self):
        """New refined aspects should be present."""
        names = {a.name for a in DEVICE_SETTINGS_ASPECTS}
        assert "ha" in names
        assert "vpn" in names
        assert "nat" in names
        assert "netflow" in names
        assert "multicast" in names
        assert "routes" in names
        assert "snmp" in names
        assert "lan_interfaces" in names

    def test_vpn_is_segment_scoped(self):
        """VPN aspect should be segment-scoped."""
        vpn = next(a for a in DEVICE_SETTINGS_ASPECTS if a.name == "vpn")
        assert vpn.segment_scoped is True


class TestAspectSearch:
    def test_search_routing(self):
        """Search for routing-related aspects."""
        registry = AspectRegistry()
        register_default_aspects(registry)

        results = registry.search("routing")
        names = {r.name for r in results}
        assert "bgp" in names
        assert "ospf" in names

    def test_search_wan(self):
        """Search for WAN-related aspects."""
        registry = AspectRegistry()
        register_default_aspects(registry)

        results = registry.search("wan")
        names = {r.name for r in results}
        assert "wan_links" in names
        assert "wan_networks" in names

    def test_search_monitoring(self):
        """Search for monitoring-related aspects."""
        registry = AspectRegistry()
        register_default_aspects(registry)

        results = registry.search("monitoring")
        names = {r.name for r in results}
        assert "netflow" in names
        assert "snmp" in names
