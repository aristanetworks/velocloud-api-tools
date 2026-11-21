"""Every list field of the configuration models has a kind.

``list_kind`` classifies a field; the walk below visits every class reachable
from the five module data classes and the test records which lists are still
undeclared, so a new list of models cannot slip in without a decision.
"""

from collections import Counter
from collections.abc import Iterator
from typing import get_args

from pyvelo.client.models.configuration.control_plane.root import ControlPlaneData
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.core import VcoModel
from pyvelo.core.base import ListKind, list_kind

from .patch_models import Root

ROOTS = (FirewallData, QosData, DeviceSettingsData, WanData, ControlPlaneData)

# Lists of models with neither a KeyField nor Positional(): a patch replaces
# them whole. Declaring one (or adding a new list) means changing this set.
UNDECLARED: frozenset[str] = frozenset(
    {
        "BgpBase.aggregation",
        "EdgeToEdgeDetail.vpn_hubs",
        "EdgeToEdgeHub.vpn_hubs",
        "LanDhcpServerV6.fixed_ip",
        "LanNetwork.fixed_ips",
        "LanNetwork.secondary_ips",
        "LanPrefixDelegation.pd_list",
        "MacAllowList.allowed_macs",
        "ModelInterfaces.routed_interfaces",
        "ModelLanInterfaces.interfaces",
        "NetflowCollector.filter_list",
        "NtpServer.keys",
        "PrefixDelegation.pd_list",
        "RadiusAuthentication.mac_bypass",
        "RouteConfig.backhaul_edges",
        "RoutedInterfaceCell.subinterfaces",
        "RoutedInterfaceGe.subinterfaces",
        "RoutedInterfaceLag.slave_interfaces",
        "RoutedInterfaceLag.subinterfaces",
        "RoutedInterfaceSfpDsl.subinterfaces",
        "RoutedInterfaceSfpGpon.subinterfaces",
        "RoutedInterfaceSfpStandard.subinterfaces",
        "RoutedInterfaceUsb.subinterfaces",
        "SegmentBfd.rules",
        "SegmentBfd.rules_v6",
        "SegmentBgp.aggregation",
        "SegmentCss.sites",
        "SegmentDns.backup_provider",
        "SegmentDns.local_dns",
        "SegmentDns.primary_provider",
        "SegmentDns.private_providers",
        "SegmentEdgeDirect.providers",
        "SegmentHandoffGateways.gateway_list",
        "SegmentHandoffGateways.gateways",
        "SegmentNetflow.collectors",
        "SegmentOspf.aggregation",
        "SegmentOspf.areas",
        "SegmentOspfBase.aggregation",
        "SegmentOspfBase.areas",
        "SegmentRoutes.nsd",
        "SegmentRoutes.nsd_v6",
        "SegmentRoutes.static",
        "SegmentRoutes.static_v6",
        "SegmentSyslog.collectors",
        "SegmentVpn.backhaul_edges",
        "WanLinkCos.classes_of_service",
        "ZscalerConfig.sub_locations",
    }
)


def _models_in(annotation: object) -> Iterator[type[VcoModel]]:
    """Every model class named anywhere inside an annotation."""
    if isinstance(annotation, type) and issubclass(annotation, VcoModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models_in(arg)


def walk(roots: tuple[type[VcoModel], ...]) -> dict[str, ListKind]:
    """Map ``"Class.field"`` to its kind for every list or map field reachable from *roots*.

    Reachable means through sub-model fields, list elements, union members and
    the values of maps of models; each class is visited once.
    """
    kinds: dict[str, ListKind] = {}
    seen: set[type[VcoModel]] = set()
    todo = list(roots)
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for name, fi in cls.model_fields.items():
            if (kind := list_kind(fi)) is not None:
                kinds[f"{cls.__name__}.{name}"] = kind
            todo.extend(_models_in(fi.annotation))
    return kinds


def undeclared(roots: tuple[type[VcoModel], ...]) -> frozenset[str]:
    return frozenset(name for name, kind in walk(roots).items() if kind == "undeclared")


def test_kind_of_each_synthetic_field():
    kinds = {name: list_kind(fi) for name, fi in Root.model_fields.items()}
    assert kinds["rules"] == "keyed"
    assert kinds["ports"] == kinds["names"] == "value"
    assert kinds["seq"] == "positional"
    assert kinds["hops"] == kinds["hops_safe"] == "undeclared"
    assert kinds["blobs"] == "untyped"
    assert kinds["by_name"] == "map"
    assert kinds["labels"] is None
    assert kinds["count"] is None


def test_undeclared_lists_are_the_recorded_set():
    assert undeclared(ROOTS) == UNDECLARED


def test_list_counts():
    assert Counter(walk(ROOTS).values()) == {
        "keyed": 20,
        "value": 19,
        "positional": 24,
        "undeclared": 47,
        "untyped": 6,
        "map": 2,
    }
