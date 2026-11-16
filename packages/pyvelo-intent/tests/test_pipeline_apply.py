"""Tests for ConfigPipeline.apply()."""

from datetime import UTC, datetime

import pytest
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
)
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    QosConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import (
    make_firewall_module,
    make_firewall_segment,
)

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _make_device_settings_module(
    mod_id: int, config_id: int
) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=DeviceSettingsData(lan=LanSettings(), segments=[]),
        created=_TS,
        modified=_TS,
        id_=mod_id,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=config_id,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_qos_module(mod_id: int, config_id: int) -> QosConfigurationModule:
    return QosConfigurationModule(
        name="QOS",
        data=QosData(),
        created=_TS,
        modified=_TS,
        id_=mod_id,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=config_id,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(
    edge_segments: list[FirewallSegment] | None = None,
    profile_segments: list[FirewallSegment] | None = None,
    *,
    include_device_settings: bool = False,
    include_qos: bool = False,
) -> ConfigState:
    edge_modules: list[AnyConfigurationModule] = [
        make_firewall_module(mod_id=1, config_id=10, segments=edge_segments)
    ]
    profile_modules: list[AnyConfigurationModule] = [
        make_firewall_module(mod_id=2, config_id=20, segments=profile_segments)
    ]
    if include_device_settings:
        edge_modules.append(_make_device_settings_module(mod_id=3, config_id=10))
        profile_modules.append(_make_device_settings_module(mod_id=4, config_id=20))
    if include_qos:
        edge_modules.append(_make_qos_module(mod_id=5, config_id=10))
        profile_modules.append(_make_qos_module(mod_id=6, config_id=20))
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=10,
        modules=edge_modules,
    )
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=profile_modules,
    )
    state = ConfigState(
        edge_config=edge_config,
        profile_config=profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    return state


class TestResolveModuleName:
    @pytest.mark.asyncio
    async def test_firewall(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        state = await pipeline.apply(
            state, module="firewall", patch={"firewall_enabled": False}
        )
        assert state.edge_config is not None
        assert state.edge_config.firewall.data.firewall_enabled is False

    @pytest.mark.asyncio
    async def test_device_settings_snake_case(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_device_settings=True)
        state = await pipeline.apply(
            state, module="device_settings", patch={"ntp": {"enabled": True}}
        )
        assert ConfigurationModuleName.DEVICE_SETTINGS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_qos_uppercase(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state(include_qos=True)
        state = await pipeline.apply(
            state,
            module="QOS",
            patch={"qos_client_connector": {"data": {"enabled": True}}},
        )
        assert ConfigurationModuleName.QOS in state.get_changed_modules()

    @pytest.mark.asyncio
    async def test_unknown_raises(self):
        pipeline = ConfigPipeline(client=None)
        state = _make_state()
        with pytest.raises(ValueError, match="Unknown module"):
            _ = await pipeline.apply(state, module="nonexistent", patch={})


@pytest.mark.asyncio
async def test_apply_simple_patch():
    """Apply a simple scalar patch to firewall module."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state()

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    assert state.edge_config is not None
    assert state.edge_config.firewall.data.firewall_enabled is False


@pytest.mark.asyncio
async def test_apply_keyed_list_merge():
    """Apply a patch that merges into a KeyField list (segments by name)."""
    pipeline = ConfigPipeline(client=None)

    global_seg = make_firewall_segment("Global Segment", 0)
    state = _make_state(edge_segments=[global_seg], profile_segments=[global_seg])

    # Add an outbound rule to the Global Segment
    state = await pipeline.apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block-ssh": {
                            "name": "block-ssh",
                            "action": {"allow_or_deny": "deny"},
                            "match": {"proto": 6, "dport_low": 22, "dport_high": 22},
                        }
                    }
                }
            }
        },
    )

    assert state.edge_config is not None
    fw_data = state.edge_config.firewall.data
    seg = fw_data.get_segment(name="Global Segment")
    assert len(seg.outbound) == 1
    assert seg.outbound[0].name == "block-ssh"
    assert seg.outbound[0].match.proto == 6


@pytest.mark.asyncio
async def test_apply_detects_change():
    """After apply, get_changed_modules should report the module as changed."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state()

    assert state.get_changed_modules() == []

    state = await pipeline.apply(
        state,
        module="firewall",
        patch={"firewall_enabled": False},
    )

    assert "firewall" in state.get_changed_modules()


@pytest.mark.asyncio
async def test_apply_camel_case_module_name():
    """Public module names should resolve the canonical camelCase spelling."""
    pipeline = ConfigPipeline(client=None)
    state = _make_state(include_device_settings=True)
    state = await pipeline.apply(
        state,
        module="deviceSettings",
        patch={"ntp": {"enabled": True}},
    )

    assert ConfigurationModuleName.DEVICE_SETTINGS in state.get_changed_modules()
