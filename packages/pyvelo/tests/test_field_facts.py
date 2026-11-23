"""Facts about a model field that a schema view needs."""

import pytest
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterfaceCell,
    RoutedInterfaceGe,
    RoutedInterfaceLag,
    RoutedInterfaceSfpDsl,
    RoutedInterfaceSfpGpon,
    RoutedInterfaceSfpStandard,
    RoutedInterfaceUsb,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    BgpNeighbor,
    CssGreSite,
    CssIpsecSite,
    DnsProvider,
    SegmentCss,
    SegmentDns,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.core import (
    ConfigContext,
    Path,
    VcoModel,
    field_models,
    python_name,
    wire_name,
)
from pyvelo.core.rebase import python_path

from .patch_models import Hop, Root, Rule, Sub


def test_wire_name_is_the_key_a_field_is_written_under():
    assert wire_name(Root, "by_name") == "byName"
    assert wire_name(FirewallData, "firewall_enabled") == "firewall_enabled"
    assert wire_name(FirewallData, "inbound_v6") == "inboundV6"
    assert wire_name(BgpNeighbor, "md5_password") == "md5Password"
    assert wire_name(BgpNeighbor, "id_") == "id"
    assert wire_name(DeviceSettingsData, "global_ipv6_settings") == "globalIPv6Settings"
    with pytest.raises(ValueError, match="Root has no field 'nope'"):
        _ = wire_name(Root, "nope")


def test_python_name_reads_every_key_a_patch_accepts():
    for key in ("inboundV6", "inbound_v6"):
        assert python_name(FirewallData, key) == "inbound_v6"
    for key in ("firewallEnabled", "firewall_enabled"):
        assert python_name(FirewallData, key) == "firewall_enabled"
    assert python_name(BgpNeighbor, "id") == "id_"
    assert python_name(FirewallData, "nope") is None


def test_python_path_and_python_name_give_one_answer():
    for key in ("inboundV6", "inbound_v6", "firewallEnabled", "nope"):
        expected = python_name(FirewallData, key) or key
        assert python_path(Path((key,)), FirewallData).parts == (expected,)


def models(model: type[VcoModel], name: str) -> tuple[type[VcoModel], ...]:
    return field_models(model.model_fields[name])


def test_field_models_are_the_classes_a_field_can_hold():
    assert models(Root, "sub") == (Sub,)
    assert models(Root, "maybe") == (Sub,)
    assert models(Root, "rules") == (Rule,)
    assert models(Root, "hops_safe") == (Hop,)
    assert models(Root, "by_name") == (Sub,)
    for name in ("ports", "blobs", "labels", "count"):
        assert models(Root, name) == ()
    assert models(DeviceSettingsData, "routed_interfaces") == (
        RoutedInterfaceGe,
        RoutedInterfaceSfpStandard,
        RoutedInterfaceSfpDsl,
        RoutedInterfaceSfpGpon,
        RoutedInterfaceCell,
        RoutedInterfaceLag,
        RoutedInterfaceUsb,
    )
    assert models(SegmentCss, "sites") == (CssIpsecSite, CssGreSite)
    assert models(SegmentDns, "primary_provider") == (DnsProvider,)


def test_whether_a_field_exists_at_a_level_is_in_the_context_view():
    edge = DeviceSettingsData.context(ConfigContext.EDGE)
    profile = DeviceSettingsData.context(ConfigContext.PROFILE)
    assert edge.loopback_interfaces.is_present is True
    assert profile.loopback_interfaces.is_present is False
    assert (edge.models.is_present, profile.models.is_present) == (False, True)
