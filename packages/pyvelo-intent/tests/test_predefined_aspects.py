"""Tests for predefined firewall and device settings aspects."""

from datetime import UTC, datetime
from typing import cast

import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import (
    LanNetwork,
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    Ntp,
    NtpServerEntry,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    DnsProvider,
    SegmentDns,
    SegmentSyslog,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_action import OutboundRuleAction
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.aspect import AspectRegistry
from pyvelo_intent.config.aspects import register_default_aspects
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState


def _as_object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_list_of_object_dicts(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    items = cast(list[object], value)
    return [_as_object_dict(item) for item in items]


def _seg_meta(name: str = "Global Segment", segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
    )


def _make_default_registry() -> AspectRegistry:
    reg = AspectRegistry()
    register_default_aspects(reg)
    return reg


def _make_pipeline() -> ConfigPipeline:
    return ConfigPipeline(client=None, aspect_registry=_make_default_registry())


def _make_fw_module(
    segments: list[FirewallSegment] | None = None,
) -> FirewallConfigurationModule:
    data = FirewallData(segments=segments or [])
    return FirewallConfigurationModule(
        name="firewall",
        data=data,
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        id_=1,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_ds_module(
    segments: list[DeviceSegment] | None = None,
    ntp: Ntp | None = None,
    lan: LanSettings | None = None,
) -> DeviceSettingsConfigurationModule:
    data = DeviceSettingsData(
        lan=lan or LanSettings(),
        segments=segments or [],
        ntp=ntp or Ntp(),
    )
    return DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=data,
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        id_=2,
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
            name="Default Profile", id_=20, modules=modules
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


class TestRegisterDefaultAspects:
    def test_all_registered(self):
        reg = _make_default_registry()
        all_names = {a.name for a in reg.list_aspects()}
        # Firewall
        assert "firewall_rules" in all_names
        # Device settings
        assert "dns" in all_names
        assert "vlans" in all_names
        assert "interfaces" in all_names
        assert "ntp" in all_names
        assert "syslog" in all_names

    def test_segment_scoped_flags(self):
        reg = _make_default_registry()
        assert reg.get("firewall_rules").segment_scoped is True
        assert reg.get("dns").segment_scoped is True
        assert reg.get("syslog").segment_scoped is True
        assert reg.get("ntp").segment_scoped is False
        assert reg.get("vlans").segment_scoped is False
        assert reg.get("interfaces").segment_scoped is False


class TestFirewallRulesAspect:
    def test_read(self):
        seg = FirewallSegment(
            segment=_seg_meta(),
            outbound=[
                FirewallOutboundRule(
                    name="allow-all",
                    match=OutboundRuleMatch(),
                    action=OutboundRuleAction(allow_or_deny="allow"),
                )
            ],
        )
        state = _make_state([_make_fw_module(segments=[seg])])
        pipeline = _make_pipeline()
        result = _as_list_of_object_dicts(
            pipeline.read_aspect(state, "firewall_rules", segment="Global Segment")
        )
        assert len(result) == 1
        assert result[0]["name"] == "allow-all"

    @pytest.mark.anyio
    async def test_write(self):
        seg = FirewallSegment(segment=_seg_meta(), outbound=[])
        state = _make_state([_make_fw_module(segments=[seg])])
        pipeline = _make_pipeline()
        state = await pipeline.apply_aspect(
            state,
            "firewall_rules",
            {
                "block-ssh": {
                    "name": "block-ssh",
                    "match": {},
                    "action": {"allow_or_deny": "deny"},
                }
            },
            segment="Global Segment",
        )
        result = _as_list_of_object_dicts(
            pipeline.read_aspect(state, "firewall_rules", segment="Global Segment")
        )
        assert len(result) == 1
        assert result[0]["name"] == "block-ssh"


class TestDnsAspect:
    def test_read(self):
        dns = SegmentDns(
            primary_provider=DnsProvider(ref="deviceSettings:dns:primaryProvider"),
            backup_provider=DnsProvider(ref="deviceSettings:dns:backupProvider"),
            private_providers=DnsProvider(ref="deviceSettings:dns:privateProviders"),
        )
        ds_seg = DeviceSegment(segment=_seg_meta(), dns=dns)
        state = _make_state([_make_ds_module(segments=[ds_seg])])
        pipeline = _make_pipeline()
        result = _as_object_dict(
            pipeline.read_aspect(state, "dns", segment="Global Segment")
        )
        assert "primary_provider" in result

    @pytest.mark.anyio
    async def test_write(self):
        dns = SegmentDns(
            primary_provider=DnsProvider(ref="deviceSettings:dns:primaryProvider"),
            backup_provider=DnsProvider(ref="deviceSettings:dns:backupProvider"),
            private_providers=DnsProvider(ref="deviceSettings:dns:privateProviders"),
        )
        ds_seg = DeviceSegment(segment=_seg_meta(), dns=dns)
        state = _make_state([_make_ds_module(segments=[ds_seg])])
        pipeline = _make_pipeline()
        state = await pipeline.apply_aspect(
            state, "dns", {"source_interface": "GE1"}, segment="Global Segment"
        )
        result = _as_object_dict(
            pipeline.read_aspect(state, "dns", segment="Global Segment")
        )
        assert result["source_interface"] == "GE1"


class TestNtpAspect:
    def test_read(self):
        ntp = Ntp(enabled=True, servers=[NtpServerEntry(server="pool.ntp.org")])
        state = _make_state([_make_ds_module(ntp=ntp)])
        pipeline = _make_pipeline()
        result = _as_object_dict(pipeline.read_aspect(state, "ntp"))
        assert result["enabled"] is True

    @pytest.mark.anyio
    async def test_write(self):
        ntp = Ntp(enabled=False)
        state = _make_state([_make_ds_module(ntp=ntp)])
        pipeline = _make_pipeline()
        state = await pipeline.apply_aspect(state, "ntp", {"enabled": True})
        result = _as_object_dict(pipeline.read_aspect(state, "ntp"))
        assert result["enabled"] is True


class TestVlansAspect:
    def test_read(self):
        network = LanNetwork(name="Corp", vlan_id=100, cidr_ip="10.0.1.0")
        lan = LanSettings(networks=[network])
        state = _make_state([_make_ds_module(lan=lan)])
        pipeline = _make_pipeline()
        result = _as_list_of_object_dicts(pipeline.read_aspect(state, "vlans"))
        assert len(result) == 1
        assert result[0]["name"] == "Corp"

    @pytest.mark.anyio
    async def test_write_update(self):
        network = LanNetwork(name="Corp", vlan_id=100, cidr_ip="10.0.1.0")
        lan = LanSettings(networks=[network])
        state = _make_state([_make_ds_module(lan=lan)])
        pipeline = _make_pipeline()
        # Update via KeyField merge (vlan_id is the key)
        state = await pipeline.apply_aspect(
            state, "vlans", {"100": {"advertise": False}}
        )
        result = _as_list_of_object_dicts(pipeline.read_aspect(state, "vlans"))
        assert result[0]["advertise"] is False


class TestSyslogAspect:
    def test_read(self):
        syslog = SegmentSyslog(enabled=True)
        ds_seg = DeviceSegment(segment=_seg_meta(), syslog=syslog)
        state = _make_state([_make_ds_module(segments=[ds_seg])])
        pipeline = _make_pipeline()
        result = _as_object_dict(
            pipeline.read_aspect(state, "syslog", segment="Global Segment")
        )
        assert result["enabled"] is True
