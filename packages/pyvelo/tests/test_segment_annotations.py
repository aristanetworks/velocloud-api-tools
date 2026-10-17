"""Tests for segment sub-model annotations."""

from pyvelo.client.models.configuration.device_settings.segment import (
    BgpBase,
    SegmentBfd,
    SegmentBgp,
    SegmentCss,
    SegmentDns,
    SegmentEcmp,
    SegmentEdgeDirect,
    SegmentHandoffGateways,
    SegmentMulticast,
    SegmentNat,
    SegmentNetflow,
    SegmentRoutes,
    SegmentSyslog,
)
from pyvelo.core.fields import get_key_field, has_override_flag


class TestSegmentOverrideFlags:
    def test_bfd(self):
        assert has_override_flag(SegmentBfd.model_fields["override"])

    def test_edge_direct(self):
        assert has_override_flag(SegmentEdgeDirect.model_fields["override"])

    def test_handoff_gateways(self):
        assert has_override_flag(SegmentHandoffGateways.model_fields["override"])

    def test_multicast(self):
        assert has_override_flag(SegmentMulticast.model_fields["override"])

    def test_nat(self):
        assert has_override_flag(SegmentNat.model_fields["override"])

    def test_netflow(self):
        assert has_override_flag(SegmentNetflow.model_fields["override"])

    def test_syslog(self):
        assert has_override_flag(SegmentSyslog.model_fields["override"])

    def test_dns(self):
        assert has_override_flag(SegmentDns.model_fields["override"])

    def test_css(self):
        assert has_override_flag(SegmentCss.model_fields["override"])

    def test_ecmp(self):
        assert has_override_flag(SegmentEcmp.model_fields["override"])

    def test_bgp(self):
        assert has_override_flag(SegmentBgp.model_fields["override"])

    def test_css_default_is_false(self):
        css = SegmentCss.model_validate({})
        assert css.override is False


class TestSegmentKeyFields:
    def test_bgp_neighbors(self):
        assert get_key_field(BgpBase.model_fields["neighbors"]) == "neighbor_ip"

    def test_bgp_filters(self):
        assert get_key_field(BgpBase.model_fields["filters"]) == "name"

    def test_icmp_probes(self):
        assert get_key_field(SegmentRoutes.model_fields["icmp_probes"]) == "name"

    def test_icmp_responders(self):
        assert get_key_field(SegmentRoutes.model_fields["icmp_responders"]) == "name"
