"""Tests for edge schema models and field typing."""

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.edge import HaRole
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    ImageUpdateData,
    ManagementPlaneData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.control_plane.root import ControlPlaneData
from pyvelo.client.models.configuration.device_settings.segment import (
    DnsProvider,
    SegmentAuthentication,
    SegmentDns,
)
from pyvelo.client.schemas.edge import (
    CompositeDeviceSettingsModule,
    CompositeFirewallModule,
    CompositeManagementPlaneModule,
    EdgeConfigurationModulesResult,
    GetEdgeConfigurationModules,
    GetEdgeSdwanPeers,
    GetHaInterfaces,
)
from pyvelo.core import PaginatedVcoModel, VcoModel

from ._type_support import require_present


class TestGetEdgeSdwanPeersNotPaginated:
    def test_base_class(self):
        assert issubclass(GetEdgeSdwanPeers, VcoModel)
        assert not issubclass(GetEdgeSdwanPeers, PaginatedVcoModel)

    def test_schema_has_no_next_page_link(self):
        schema = GetEdgeSdwanPeers.model_json_schema()
        assert "nextPageLink" not in schema.get("properties", {})


class TestGetHaInterfacesRole:
    _EDGE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_role_accepts_enum_value(self):
        obj = GetHaInterfaces.model_validate(
            {"edgeLogicalId": self._EDGE_UUID, "role": "ACTIVE"}
        )
        assert obj.role == HaRole.ACTIVE

    def test_role_rejects_invalid_value(self):
        with pytest.raises(Exception):
            GetHaInterfaces.model_validate(
                {"edgeLogicalId": self._EDGE_UUID, "role": "INVALID"}
            )

    def test_schema_exposes_enum_values(self):
        schema = GetHaInterfaces.model_json_schema()
        defs = schema.get("$defs", {})
        ha_role = defs.get("HaRole", {})
        assert set(ha_role.get("enum", [])) == {"ACTIVE", "STANDBY"}


# ---------------------------------------------------------------------------
# GetEdgeConfigurationModules request
# ---------------------------------------------------------------------------

_EDGE_UUID = "00000000-0000-4000-8000-000000000001"


class TestGetEdgeConfigurationModulesRequest:
    def test_accepts_edge_id(self):
        req = GetEdgeConfigurationModules(edge_id=42)
        assert req.edge_id == 42
        assert req.edge_logical_id is MISSING

    def test_accepts_edge_logical_id(self):
        req = GetEdgeConfigurationModules.model_validate({"edgeLogicalId": _EDGE_UUID})
        assert req.edge_id is MISSING
        assert str(req.edge_logical_id) == _EDGE_UUID

    def test_rejects_missing_both_identifiers(self):
        with pytest.raises(Exception, match="edgeId or edgeLogicalId"):
            GetEdgeConfigurationModules()

    def test_accepts_enterprise_logical_id(self):
        req = GetEdgeConfigurationModules.model_validate(
            {
                "edgeLogicalId": _EDGE_UUID,
                "enterpriseLogicalId": "00000000-0000-4000-8000-000000000002",
            }
        )
        assert req.enterprise_logical_id is not MISSING


# ---------------------------------------------------------------------------
# EdgeConfigurationModulesResult response
# ---------------------------------------------------------------------------

_FIRMWARE_DATA = {
    "buildNumber": "R550-20240101-GA",
    "profileDeviceFamily": "EDGE5X0",
    "profileVersion": "4.5.0",
    "softwarePackageId": 100,
    "softwarePackageName": "edge550-4.5.0",
    "version": "4.5.0",
    "windowDurationMins": 120,
    "windowed": False,
    "devicefamily": ["EDGE5X0"],
}

_COMPOSITE_RESPONSE = {
    "deviceSettings": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {
            "lan": {"management": {"cidrIp": "192.168.2.1", "cidrPrefix": 24}},
            "segments": [],
        },
    },
    "firewall": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {},
    },
    "QOS": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {},
    },
    "WAN": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {},
    },
    "controlPlane": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {
            "gatewaySelection": {
                "primary": "auto",
                "secondary": "auto",
                "mode": "dynamic",
            }
        },
    },
    "managementPlane": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {
            "heartBeatSeconds": 30,
            "managementPlaneProxy": {"primary": "vco1.example.com", "secondary": None},
            "statsUploadSeconds": 300,
            "timeSliceSeconds": 60,
        },
    },
    "imageUpdate": {
        "version": "1718888888000",
        "schemaVersion": "3.0.0",
        "type": "ENTERPRISE",
        "data": {
            "buildNumber": "R550-20240101-GA",
            "profileDeviceFamily": "EDGE5X0",
            "profileVersion": "4.5.0",
            "softwarePackageId": 100,
            "softwarePackageName": "edge550-4.5.0",
            "version": "4.5.0",
            "windowDurationMins": 120,
            "windowed": False,
            "devicefamily": ["EDGE5X0"],
            "modemFirmware": _FIRMWARE_DATA,
            "platformFirmware": _FIRMWARE_DATA,
            "factoryFirmware": _FIRMWARE_DATA,
        },
    },
}


class TestEdgeConfigurationModulesResult:
    def test_parses_full_response(self):
        result = EdgeConfigurationModulesResult.model_validate(_COMPOSITE_RESPONSE)
        device_settings = require_present(result.device_settings)
        firewall = require_present(result.firewall)
        qos = require_present(result.qos)
        wan = require_present(result.wan)
        control_plane = require_present(result.control_plane)
        management_plane = require_present(result.management_plane)
        image_update = require_present(result.image_update)
        assert isinstance(device_settings, CompositeDeviceSettingsModule)
        assert isinstance(firewall, CompositeFirewallModule)
        assert qos is not None
        assert wan is not None
        assert control_plane is not None
        assert isinstance(management_plane, CompositeManagementPlaneModule)
        assert image_update is not None
        assert isinstance(require_present(device_settings.data), DeviceSettingsData)
        assert isinstance(require_present(firewall.data), FirewallData)
        assert isinstance(require_present(qos.data), QosData)
        assert isinstance(require_present(wan.data), WanData)
        assert isinstance(require_present(control_plane.data), ControlPlaneData)
        assert isinstance(require_present(management_plane.data), ManagementPlaneData)
        assert isinstance(require_present(image_update.data), ImageUpdateData)

    def test_module_type_is_parsed(self):
        result = EdgeConfigurationModulesResult.model_validate(_COMPOSITE_RESPONSE)
        firewall = require_present(result.firewall)
        assert firewall is not None
        assert firewall.type_ == ConfigurationModuleType.ENTERPRISE

    def test_management_plane_fields(self):
        result = EdgeConfigurationModulesResult.model_validate(_COMPOSITE_RESPONSE)
        management_plane = require_present(result.management_plane)
        assert management_plane is not None
        mp = require_present(management_plane.data)
        assert mp is not None
        assert mp.heart_beat_seconds == 30
        assert mp.management_plane_proxy.primary == "vco1.example.com"
        assert mp.management_plane_proxy.secondary is None
        assert mp.stats_upload_seconds == 300

    def test_image_update_fields(self):
        result = EdgeConfigurationModulesResult.model_validate(_COMPOSITE_RESPONSE)
        image_update = require_present(result.image_update)
        assert image_update is not None
        iu = require_present(image_update.data)
        assert iu is not None
        assert iu.build_number == "R550-20240101-GA"
        assert iu.devicefamily == ["EDGE5X0"]
        assert iu.modem_firmware.software_package_id == 100
        assert iu.windowed is False

    def test_partial_response_only_firewall(self):
        partial = {
            "firewall": {
                "version": "1718888888000",
                "schemaVersion": "3.0.0",
                "type": "ENTERPRISE",
                "data": {},
            }
        }
        result = EdgeConfigurationModulesResult.model_validate(partial)
        assert isinstance(result.firewall, CompositeFirewallModule)
        assert result.device_settings is MISSING
        assert result.qos is MISSING
        assert result.management_plane is MISSING

    def test_null_module_value(self):
        payload = {"firewall": None, "QOS": None}
        result = EdgeConfigurationModulesResult.model_validate(payload)
        assert result.firewall is None
        assert result.qos is None

    def test_effective_device_settings_accept_nullable_segment_defaults(self):
        payload = {
            "deviceSettings": {
                "version": "1718888888000",
                "schemaVersion": "3.0.0",
                "type": "ENTERPRISE",
                "data": {
                    "lan": {
                        "management": {
                            "cidrIp": "192.168.2.1",
                            "cidrPrefix": 24,
                        }
                    },
                    "segments": [
                        {
                            "segment": {"segmentId": 0, "name": "Global Segment"},
                            "bgp": {
                                "enabled": False,
                                "connectedRoutes": None,
                                "ospf": None,
                                "defaultRoute": None,
                                "v6Detail": {
                                    "connectedRoutes": None,
                                    "ospf": None,
                                    "defaultRoute": None,
                                },
                            },
                            "authentication": {},
                            "dns": {
                                "primaryProvider": {
                                    "isPrivate": False,
                                    "primary": "198.51.100.53",
                                    "ipv4Servers": ["198.51.100.53"],
                                    "name": "Example DNS Provider",
                                },
                                "backupProvider": {},
                                "privateProviders": {},
                            },
                        }
                    ],
                },
            }
        }

        result = EdgeConfigurationModulesResult.model_validate(payload)
        device_settings = require_present(result.device_settings)
        assert device_settings is not None
        data = require_present(device_settings.data)
        assert data is not None
        segments = require_present(data.segments)
        assert segments is not None
        segment = segments[0]

        bgp = require_present(segment.bgp)
        v6_detail = require_present(bgp.v6_detail)
        assert bgp.connected_routes is True
        assert require_present(bgp.ospf).enabled is False
        assert require_present(bgp.default_route).enabled is False
        assert v6_detail.connected_routes is True
        assert require_present(v6_detail.ospf).enabled is False
        assert require_present(v6_detail.default_route).enabled is False
        assert isinstance(segment.authentication, SegmentAuthentication)
        assert segment.authentication.ref is MISSING
        assert (
            segment.model_dump(by_alias=True, exclude_unset=True)["authentication"]
            == {}
        )
        dns = require_present(segment.dns)
        primary_provider = require_present(dns.primary_provider)
        assert isinstance(primary_provider, DnsProvider)
        assert primary_provider.ref is MISSING
        assert primary_provider.model_extra == {
            "isPrivate": False,
            "primary": "198.51.100.53",
            "ipv4Servers": ["198.51.100.53"],
            "name": "Example DNS Provider",
        }
        backup_provider = require_present(dns.backup_provider)
        private_provider = require_present(dns.private_providers)
        assert isinstance(backup_provider, DnsProvider)
        assert isinstance(private_provider, DnsProvider)
        assert backup_provider.ref is MISSING
        assert private_provider.ref is MISSING

    def test_effective_dns_accepts_resolved_provider_arrays(self):
        dns = SegmentDns.model_validate(
            {
                "primaryProvider": {},
                "backupProvider": {},
                "privateProviders": [
                    {"name": "Private DNS A", "ipv4Servers": ["10.0.0.10"]},
                    {"name": "Private DNS B", "ipv4Servers": ["10.0.0.11"]},
                ],
            }
        )

        private_providers = require_present(dns.private_providers)
        assert isinstance(private_providers, list)
        names = []
        for provider in private_providers:
            assert provider.model_extra is not None
            names.append(provider.model_extra["name"])
        assert names == [
            "Private DNS A",
            "Private DNS B",
        ]

    def test_roundtrip_serialization(self):
        result = EdgeConfigurationModulesResult.model_validate(_COMPOSITE_RESPONSE)
        dumped = result.model_dump(by_alias=True, exclude_unset=True)
        assert "QOS" in dumped
        assert "WAN" in dumped
        assert "deviceSettings" in dumped
        reparsed = EdgeConfigurationModulesResult.model_validate(dumped)
        reparsed_firewall = require_present(reparsed.firewall)
        result_firewall = require_present(result.firewall)
        assert reparsed_firewall is not None
        assert result_firewall is not None
        assert reparsed_firewall.version == result_firewall.version
