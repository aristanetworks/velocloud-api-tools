"""Tests for typed module accessors and mutable_data on configuration classes."""

import datetime
from uuid import UUID

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    SegmentMetadata as DsSegmentMetadata,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.segment import (
    QosSegment,
)
from pyvelo.client.models.configuration.qos.segment import (
    SegmentMetadata as QosSegmentMetadata,
)
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
)

_NOW = datetime.datetime.now(tz=datetime.UTC)


def _make_ds_module(**kwargs) -> DeviceSettingsConfigurationModule:
    seg = DeviceSegment(
        segment=DsSegmentMetadata(
            segment_id=0, name="Global Segment", type_=NetworkSegmentType.REGULAR
        )
    )
    return DeviceSettingsConfigurationModule(
        created=_NOW,
        modified=_NOW,
        id_=100,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=1,
        enterprise_logical_id=None,
        schema_version="1.0",
        version="1.0",
        metadata=None,
        data=DeviceSettingsData(
            lan=LanSettings(interfaces=[], networks=[]),
            segments=[seg],
        ),
        **kwargs,
    )


def _make_qos_module(**kwargs) -> QosConfigurationModule:
    seg = QosSegment(
        segment=QosSegmentMetadata(
            segment_id=0,
            name="Global Segment",
            type_=NetworkSegmentType.REGULAR,
            segment_logical_id=UUID("00000000-0000-0000-0000-000000000000"),
        )
    )
    return QosConfigurationModule(
        created=_NOW,
        modified=_NOW,
        id_=101,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=1,
        enterprise_logical_id=None,
        schema_version="1.0",
        version="1.0",
        metadata=None,
        data=QosData(segments=[seg]),
        **kwargs,
    )


def _make_firewall_module() -> FirewallConfigurationModule:
    return FirewallConfigurationModule(
        created=_NOW,
        modified=_NOW,
        id_=102,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=1,
        enterprise_logical_id=None,
        schema_version="1.0",
        version="1.0",
        metadata=None,
        data=FirewallData(),
    )


def _make_wan_module() -> WanConfigurationModule:
    return WanConfigurationModule(
        created=_NOW,
        modified=_NOW,
        id_=103,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=1,
        enterprise_logical_id=None,
        schema_version="1.0",
        version="1.0",
        metadata=None,
        data=WanData(),
    )


def _make_edge_config(modules=None) -> EdgeConfiguration:
    return EdgeConfiguration(
        name="Edge Specific Profile",
        modules=modules if modules is not None else MISSING,
    )


class TestModuleAccessors:
    def test_device_settings_property(self):
        ds = _make_ds_module()
        config = _make_edge_config([ds])
        result = config.device_settings
        assert isinstance(result, DeviceSettingsConfigurationModule)
        assert result.id_ == 100

    def test_qos_property(self):
        qos = _make_qos_module()
        config = _make_edge_config([qos])
        result = config.qos
        assert isinstance(result, QosConfigurationModule)
        assert result.id_ == 101

    def test_firewall_property(self):
        fw = _make_firewall_module()
        config = _make_edge_config([fw])
        result = config.firewall
        assert isinstance(result, FirewallConfigurationModule)
        assert result.id_ == 102

    def test_wan_property(self):
        wan = _make_wan_module()
        config = _make_edge_config([wan])
        result = config.wan
        assert isinstance(result, WanConfigurationModule)
        assert result.id_ == 103

    def test_get_module_string(self):
        ds = _make_ds_module()
        config = _make_edge_config([ds])
        result = config.get_module(ConfigurationModuleName.DEVICE_SETTINGS)
        assert result.id_ == 100

    def test_missing_module_raises(self):
        config = _make_edge_config([_make_ds_module()])
        with pytest.raises(ValueError, match="Module 'QOS' not found"):
            config.qos

    def test_missing_modules_raises(self):
        config = _make_edge_config()
        with pytest.raises(ValueError, match="Configuration has no modules"):
            config.device_settings

    def test_works_on_enterprise_profile(self):
        ds = _make_ds_module()
        profile = EnterpriseConfigurationProfile(name="Default", modules=[ds])
        result = profile.device_settings
        assert isinstance(result, DeviceSettingsConfigurationModule)


class TestMutableData:
    def test_deep_copy_is_independent(self):
        ds = _make_ds_module()
        data_copy = ds.mutable_data()
        data_copy.segments.clear()
        assert len(ds.data.segments) == 1

    def test_qos_mutable_data(self):
        qos = _make_qos_module()
        data_copy = qos.mutable_data()
        assert isinstance(data_copy, QosData)
        data_copy.segments.clear()
        assert len(qos.data.segments) == 1

    def test_firewall_mutable_data(self):
        fw = _make_firewall_module()
        data_copy = fw.mutable_data()
        assert isinstance(data_copy, FirewallData)

    def test_wan_mutable_data(self):
        wan = _make_wan_module()
        data_copy = wan.mutable_data()
        assert isinstance(data_copy, WanData)

    def test_missing_fields_survive_deepcopy(self):
        ds = _make_ds_module()
        data_copy = ds.mutable_data()
        assert data_copy.ntp_server is MISSING
