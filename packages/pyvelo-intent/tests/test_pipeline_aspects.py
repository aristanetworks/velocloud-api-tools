"""Tests for ConfigPipeline aspect operations."""

from datetime import UTC, datetime
from typing import cast

import pytest
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    Ntp,
    NtpServerEntry,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
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
from pyvelo.core.base import VcoModel
from pyvelo_intent.config.aspect import Aspect, AspectRegistry
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.errors import UnknownRegistryEntryError

_TS = datetime(2024, 1, 1, tzinfo=UTC)


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


def _make_fw_module(
    segments: list[FirewallSegment] | None = None,
) -> FirewallConfigurationModule:
    data = FirewallData(segments=segments or [])
    return FirewallConfigurationModule(
        name="firewall",
        data=data,
        created=_TS,
        modified=_TS,
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
) -> DeviceSettingsConfigurationModule:
    data = DeviceSettingsData(
        lan=LanSettings(),
        segments=segments or [],
        ntp=ntp or Ntp(),
    )
    return DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=data,
        created=_TS,
        modified=_TS,
        id_=2,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(modules: list[AnyConfigurationModule] | None = None) -> ConfigState:
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=modules or [],
    )
    return ConfigState(
        edge_config=None,
        profile_config=profile_config,
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def _make_aspect_registry() -> AspectRegistry:
    reg = AspectRegistry()
    reg.register(
        Aspect(
            name="ntp",
            module=ConfigurationModuleName.DEVICE_SETTINGS,
            path="ntp",
            description="NTP time server settings",
            tags=["ntp", "time"],
        )
    )
    reg.register(
        Aspect(
            name="firewall_rules",
            module=ConfigurationModuleName.FIREWALL,
            path="outbound",
            description="Per-segment outbound firewall rules",
            tags=["firewall", "rules", "outbound"],
            segment_scoped=True,
        )
    )
    return reg


class TestReadAspect:
    def test_read_non_segment_scoped(self):
        ntp = Ntp(enabled=True, servers=[NtpServerEntry(server="pool.ntp.org")])
        state = _make_state([_make_ds_module(ntp=ntp)])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        result = _as_object_dict(pipeline.read_aspect(state, "ntp"))
        assert result["enabled"] is True
        servers = _as_list_of_object_dicts(result["servers"])
        assert len(servers) == 1
        assert servers[0]["server"] == "pool.ntp.org"

    def test_read_segment_scoped(self):
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
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        result = _as_list_of_object_dicts(
            pipeline.read_aspect(state, "firewall_rules", segment="Global Segment")
        )
        assert len(result) == 1
        assert result[0]["name"] == "allow-all"

    def test_read_segment_scoped_missing_segment_param(self):
        seg = FirewallSegment(segment=_seg_meta(), outbound=[])
        state = _make_state([_make_fw_module(segments=[seg])])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        with pytest.raises(ValueError, match="segment-scoped"):
            _ = pipeline.read_aspect(state, "firewall_rules")

    def test_read_unknown_aspect(self):
        state = _make_state([])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        with pytest.raises(UnknownRegistryEntryError, match="Unknown aspect"):
            _ = pipeline.read_aspect(state, "nonexistent")


class TestApplyAspect:
    @pytest.mark.anyio
    async def test_apply_non_segment_scoped(self):
        ntp = Ntp(enabled=False)
        state = _make_state([_make_ds_module(ntp=ntp)])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        state = await pipeline.apply_aspect(state, "ntp", {"enabled": True})
        result = _as_object_dict(pipeline.read_aspect(state, "ntp"))
        assert result["enabled"] is True

    @pytest.mark.anyio
    async def test_apply_segment_scoped(self):
        seg = FirewallSegment(segment=_seg_meta(), outbound=[])
        state = _make_state([_make_fw_module(segments=[seg])])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
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


class TestApplyAspects:
    @pytest.mark.anyio
    async def test_multi_aspect_apply(self):
        ntp = Ntp(enabled=False)
        seg = FirewallSegment(segment=_seg_meta(), outbound=[])
        state = _make_state([_make_ds_module(ntp=ntp), _make_fw_module(segments=[seg])])
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())

        state = await pipeline.apply_aspects(
            state,
            {
                "ntp": {"enabled": True},
                "firewall_rules": {
                    "Global Segment": {
                        "block-ssh": {
                            "name": "block-ssh",
                            "match": {},
                            "action": {"allow_or_deny": "deny"},
                        }
                    }
                },
            },
        )

        ntp_result = _as_object_dict(pipeline.read_aspect(state, "ntp"))
        assert ntp_result["enabled"] is True

        fw_result = _as_list_of_object_dicts(
            pipeline.read_aspect(state, "firewall_rules", segment="Global Segment")
        )
        assert len(fw_result) == 1


class TestListAndSearch:
    def test_list_aspects(self):
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        aspects = pipeline.list_aspects()
        names = {a.name for a in aspects}
        assert "ntp" in names
        assert "firewall_rules" in names

    def test_search_aspects(self):
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        results = pipeline.search_aspects("time")
        assert len(results) == 1
        assert results[0].name == "ntp"


class TestInspectAspect:
    def test_inspect_without_schema(self):
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        info = pipeline.inspect_aspect("ntp")
        assert info["name"] == "ntp"
        assert info["module"] == "deviceSettings"
        assert info["segment_scoped"] is False
        assert "patch_schema" not in info

    def test_inspect_with_schema(self):
        pipeline = ConfigPipeline(client=None, aspect_registry=_make_aspect_registry())
        models: dict[ConfigurationModuleName, type[VcoModel]] = {
            ConfigurationModuleName.DEVICE_SETTINGS: DeviceSettingsData
        }
        info = pipeline.inspect_aspect("ntp", module_models=models)
        assert "patch_schema" in info
        assert info["patch_schema"]["title"] == "Ntp"
