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
from pyvelo.core import KeySpec, VcoModel
from pyvelo.core.base import ListKind, list_kind
from pyvelo.core.fields import get_key_spec
from pyvelo.core.layers import Edge

from .patch_models import Root

ROOTS = (FirewallData[Edge], QosData, DeviceSettingsData, WanData, ControlPlaneData)

# Lists of models with neither a KeyField nor Positional(): a patch names their
# items by position, at(n), and adds under "+". Declaring one (or adding a new
# list) means changing this set.
UNDECLARED: frozenset[str] = frozenset(
    {
        "LanNetwork.secondary_ips",
        "LanPrefixDelegation.pd_list",
        "NtpServer.keys",
        "PrefixDelegation.pd_list",
        "RadiusAuthentication.mac_bypass",
        "RoutedInterfaceCell.subinterfaces",
        "RoutedInterfaceGe.subinterfaces",
        "RoutedInterfaceLag.subinterfaces",
        "RoutedInterfaceSfpDsl.subinterfaces",
        "RoutedInterfaceSfpGpon.subinterfaces",
        "RoutedInterfaceSfpStandard.subinterfaces",
        "RoutedInterfaceUsb.subinterfaces",
        "SegmentCss.sites",
        "SegmentDns.backup_provider",
        "SegmentDns.primary_provider",
        "SegmentDns.private_providers",
        "SegmentEdgeDirect.providers",
        "SegmentOspf.aggregation",
        "SegmentOspfBase.aggregation",
        "SegmentRoutes.nsd",
        "SegmentRoutes.nsd_v6",
        "WanLinkCos.classes_of_service",
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


def keyed_fields(
    roots: tuple[type[VcoModel], ...],
) -> Iterator[tuple[str, KeySpec, type[VcoModel]]]:
    """Each keyed field reachable from *roots*: its name, its ``KeySpec`` and an item class.

    A list of a union of models yields once per variant.
    """
    seen: set[type[VcoModel]] = set()
    todo = list(roots)
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for name, fi in cls.model_fields.items():
            if (spec := get_key_spec(fi)) is not None:
                for item in dict.fromkeys(_models_in(fi.annotation)):
                    yield f"{cls.__name__}.{name}", spec, item
            todo.extend(_models_in(fi.annotation))


def resolves(model: type[VcoModel], path: str) -> bool:
    """Whether the dotted *path* of python field names names a field of *model*."""
    *parents, leaf = path.split(".")
    for part in parents:
        fi = model.model_fields.get(part)
        sub = None if fi is None else next(_models_in(fi.annotation), None)
        if sub is None:
            return False
        model = sub
    return leaf in model.model_fields


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


def test_every_key_path_names_a_field():
    for name, spec, item in keyed_fields(ROOTS):
        for path in (*spec.paths, spec.alt, spec.label):
            if path is not None:
                assert resolves(item, path), f"{name}: {path}"


def test_list_counts():
    assert Counter(walk(ROOTS).values()) == {
        "keyed": 45,
        "value": 19,
        "positional": 24,
        "undeclared": 22,
        "untyped": 6,
        "map": 2,
    }
