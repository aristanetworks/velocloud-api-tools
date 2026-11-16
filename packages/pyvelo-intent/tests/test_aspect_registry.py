"""Tests for Aspect, AspectSummary, and AspectRegistry."""

import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo_intent.config.aspect import Aspect, AspectRegistry, AspectSummary
from pyvelo_intent.errors import UnknownRegistryEntryError


def _dns_aspect() -> Aspect:
    return Aspect(
        name="dns",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="dns",
        description="DNS server configuration",
        tags=["dns", "networking"],
        segment_scoped=True,
    )


def _ntp_aspect() -> Aspect:
    return Aspect(
        name="ntp",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="ntp",
        description="NTP time server settings",
        tags=["ntp", "time"],
    )


def _firewall_rules_aspect() -> Aspect:
    return Aspect(
        name="firewall_rules",
        module=ConfigurationModuleName.FIREWALL,
        path="outbound",
        description="Per-segment outbound firewall rules",
        tags=["firewall", "rules", "outbound"],
        segment_scoped=True,
    )


class TestAspect:
    def test_frozen_dataclass(self):
        a = _dns_aspect()
        with pytest.raises(AttributeError):
            a.__setattr__("name", "other")

    def test_defaults(self):
        a = Aspect(name="x", module=ConfigurationModuleName.META_DATA, path="p")
        assert a.description == ""
        assert a.tags == []
        assert a.examples is None
        assert a.segment_scoped is False


class TestAspectSummary:
    def test_fields(self):
        s = AspectSummary(name="dns", description="DNS", tags=["dns"])
        assert s.name == "dns"
        assert s.description == "DNS"
        assert s.tags == ["dns"]


class TestAspectRegistry:
    def test_register_and_get(self):
        reg = AspectRegistry()
        aspect = _dns_aspect()
        reg.register(aspect)
        assert reg.get("dns") is aspect

    def test_get_unknown_raises(self):
        reg = AspectRegistry()
        with pytest.raises(UnknownRegistryEntryError, match="Unknown aspect"):
            _ = reg.get("nonexistent")

    def test_register_overwrites(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        replacement = Aspect(
            name="dns",
            module=ConfigurationModuleName.DEVICE_SETTINGS,
            path="dns.new",
            description="Updated DNS",
            tags=["dns"],
        )
        reg.register(replacement)
        assert reg.get("dns").path == "dns.new"

    def test_list_aspects(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        reg.register(_ntp_aspect())
        summaries = reg.list_aspects()
        assert len(summaries) == 2
        names = {s.name for s in summaries}
        assert names == {"dns", "ntp"}
        for s in summaries:
            assert isinstance(s, AspectSummary)

    def test_list_aspects_empty(self):
        reg = AspectRegistry()
        assert reg.list_aspects() == []

    def test_search_by_name(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        reg.register(_ntp_aspect())
        reg.register(_firewall_rules_aspect())
        results = reg.search("dns")
        assert len(results) == 1
        assert results[0].name == "dns"

    def test_search_by_description(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        reg.register(_ntp_aspect())
        results = reg.search("time server")
        assert len(results) == 1
        assert results[0].name == "ntp"

    def test_search_by_tag(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        reg.register(_firewall_rules_aspect())
        results = reg.search("outbound")
        assert len(results) == 1
        assert results[0].name == "firewall_rules"

    def test_search_case_insensitive(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        results = reg.search("DNS")
        assert len(results) == 1

    def test_search_no_match(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        assert reg.search("zebra") == []

    def test_search_multiple_matches(self):
        reg = AspectRegistry()
        reg.register(_dns_aspect())
        reg.register(_ntp_aspect())
        reg.register(_firewall_rules_aspect())
        # "networking" tag on dns, but also check broader queries
        results = reg.search("networking")
        assert len(results) == 1
        assert results[0].name == "dns"
