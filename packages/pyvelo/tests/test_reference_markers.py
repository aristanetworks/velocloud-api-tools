"""``Provides``, ``RefersTo`` and ``HubRole``: the markers of identity references and hub roles, and where the models declare them."""

import dataclasses
import json
import uuid
from collections.abc import Iterator

import hub_helpers
import pytest
import segment_helpers
from hub_helpers import (
    CLUSTER,
    HUB_A,
    HUB_B,
    backhaul_entry,
    backhaul_rule,
    ds_with_vpn,
)
from pydantic.fields import FieldInfo
from pyvelo.client.models.configuration.device_settings.lan import LanNetwork
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    LoopbackInterface,
)
from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterfaceBase,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    EdgeToEdgeDetail,
    EdgeToEdgeHub,
    SegmentVpn,
    VpnHub,
)
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge, RouteConfig
from pyvelo.core import (
    HubIdentity,
    HubLogicalId,
    HubName,
    HubRole,
    HubService,
    Provides,
    RefersTo,
    SegmentIdentity,
    VcoModel,
    add,
    delete,
    field_models,
    get_hub_role,
    get_name_of,
    get_provides,
    get_refers_to,
    get_resolvable,
)
from pyvelo.core.fields import KeySpec, get_key_spec

# The routed interfaces are reached through their subclasses, so the base class is a root too.
ROOTS = (DeviceSettingsData, QosData, RoutedInterfaceBase)
GLOBAL = segment_helpers.GLOBAL


def _inherited(cls: type[VcoModel], name: str, field_info: FieldInfo) -> bool:
    """Whether a base class of *cls* declares the field *name* the same way (a subclass does not count again)."""
    return any(
        (base_info := getattr(base, "model_fields", {}).get(name)) is not None
        and base_info.metadata == field_info.metadata
        for base in cls.__mro__[1:]
    )


def _fields(
    roots: tuple[type[VcoModel], ...] = ROOTS,
) -> Iterator[tuple[type[VcoModel], str, FieldInfo]]:
    """Each field of each model class reachable from *roots*, once per class."""
    seen: set[type[VcoModel]] = set()
    todo = list(roots)
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for name, field_info in cls.model_fields.items():
            if not _inherited(cls, name, field_info):
                yield cls, name, field_info
            todo.extend(field_models(field_info))


def test_every_refers_to_names_a_set_some_provides_declares():
    provided = {p.name for _, _, fi in _fields() if (p := get_provides(fi)) is not None}
    referred = {
        r.name for _, _, fi in _fields() if (r := get_refers_to(fi)) is not None
    }
    assert referred <= provided
    assert provided == referred == {"segment", "backhaul_hub"}


def test_per_segment_agrees_on_both_sides():
    per_segment: dict[str, set[bool]] = {}
    for _, _, fi in _fields():
        if (provides := get_provides(fi)) is not None:
            per_segment.setdefault(provides.name, set()).add(provides.per_segment)
        if (refers := get_refers_to(fi)) is not None:
            per_segment.setdefault(refers.name, set()).add(refers.per_segment)
    assert per_segment == {"segment": {False}, "backhaul_hub": {True}}


def test_the_declared_references():
    found = {
        f"{cls.__name__}.{name}": refers
        for cls, name, fi in _fields()
        if (refers := get_refers_to(fi)) is not None
    }
    assert found == {
        "RoutedInterfaceBase.segment_id": RefersTo("segment", none=(-1,)),
        "LanNetwork.segment_id": RefersTo("segment"),
        "LoopbackInterface.segment_id": RefersTo("segment"),
        "BackhaulEdge.edge_logical_id": RefersTo("backhaul_hub", per_segment=True),
    }
    for model in (RoutedInterfaceBase, LanNetwork, LoopbackInterface):
        resolvable = get_resolvable(model.model_fields["segment_id"])
        assert resolvable is not None
        assert (resolvable.identity_type, resolvable.attribute) == (
            SegmentIdentity,
            "segment_id",
        )


def test_the_declared_providers():
    found = {
        f"{cls.__name__}.{name}": provides
        for cls, name, fi in _fields()
        if (provides := get_provides(fi)) is not None
    }
    assert found == {
        "DeviceSettingsData.segments": Provides("segment", key="segment.segment_id"),
        "SegmentVpn.backhaul_edges": Provides(
            "backhaul_hub", key="logical_id", per_segment=True
        ),
    }


def test_a_provides_key_names_a_field_of_the_items():
    for cls, name, fi in _fields():
        if (provides := get_provides(fi)) is None:
            continue
        (item,) = field_models(fi)
        *parents, leaf = provides.key.split(".")
        for part in parents:
            (item,) = field_models(item.model_fields[part])
        assert leaf in item.model_fields, f"{cls.__name__}.{name}"


def test_the_hub_roles():
    found = {
        f"{cls.__name__}.{name}": role.role
        for cls, name, fi in _fields((DeviceSettingsData,))
        if (role := get_hub_role(fi)) is not None
    }
    assert found == {
        "EdgeToEdgeHub.vpn_hubs": "hub",
        "EdgeToEdgeDetail.vpn_hubs": "bridge",
        "SegmentVpn.backhaul_edges": "backhaul",
    }
    assert HubRole("hub") == get_hub_role(EdgeToEdgeHub.model_fields["vpn_hubs"])


def test_the_hub_list_fields_are_the_marked_fields_with_their_wire_paths():
    from pyvelo.client.hub_services import hub_list_fields

    marked = [
        f"{cls.__name__}.{name}"
        for cls, name, fi in _fields((DeviceSettingsData,))
        if get_hub_role(fi) is not None
    ]
    declared = {(f.role, ".".join(f.path)) for f in hub_list_fields()}
    assert declared == {
        ("hub", "vpn.edgeToEdgeHub.vpnHubs"),
        ("bridge", "vpn.edgeToEdgeDetail.vpnHubs"),
        ("backhaul", "vpn.backHaulEdges"),
    }
    assert len(hub_list_fields()) == len(marked)


def test_the_hub_lists_are_keyed_by_logical_id_and_labelled_by_name():
    for model, name in (
        (EdgeToEdgeHub, "vpn_hubs"),
        (EdgeToEdgeDetail, "vpn_hubs"),
        (SegmentVpn, "backhaul_edges"),
    ):
        assert get_key_spec(model.model_fields[name]) == KeySpec(
            ("logical_id",), None, "name"
        )
    assert get_key_spec(RouteConfig.model_fields["backhaul_edges"]) == KeySpec(
        ("edge_logical_id",), "logical_id", "edge_name"
    )


def test_a_hub_list_and_a_backhaul_entry_round_trip():
    entry = ds_with_vpn(
        GLOBAL, hubs=(HUB_A,), bridges=(HUB_B,), backhaul=(CLUSTER,), edge_to_edge=True
    )
    segment = DeviceSegment.model_validate(entry)
    vpn = segment.vpn.model_dump(by_alias=True, mode="json")
    wire = entry["vpn"]
    assert vpn["edgeToEdgeHub"]["vpnHubs"] == wire["edgeToEdgeHub"]["vpnHubs"]
    assert vpn["edgeToEdgeDetail"]["vpnHubs"] == wire["edgeToEdgeDetail"]["vpnHubs"]
    assert vpn["backHaulEdges"] == wire["backHaulEdges"]

    rule_entry = backhaul_entry(HUB_A, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    rule = backhaul_rule("Box", rule_entry)
    action = rule["action"]["edge2EdgeRouteAction"]
    route = RouteConfig.model_validate(action["routeCfg"])
    dumped = route.model_dump(by_alias=True, mode="json", exclude_unset=True)
    assert dumped["backhaulEdges"] == [rule_entry]
    assert json.loads(route.model_dump_json(by_alias=True))["backhaulEdges"] == [
        rule_entry
    ]


def test_a_backhaul_entry_without_edge_id_dumps_without_it():
    entry = backhaul_entry(HUB_A, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    del entry["edgeId"]
    parsed = BackhaulEdge.model_validate(entry)
    assert parsed.model_dump(by_alias=True, mode="json") == entry


def test_a_backhaul_entry_without_edge_logical_id_round_trips_unchanged():
    entry = backhaul_entry(HUB_A, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    del entry["edgeLogicalId"]
    del entry["edgeId"]
    del entry["name"]
    parsed = BackhaulEdge.model_validate(entry)
    assert parsed.model_dump(by_alias=True, mode="json") == entry


def test_the_alt_key_selects_a_backhaul_entry_by_its_service():
    service = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    other = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    route = RouteConfig.model_validate(
        {
            "backhaulEdges": [
                backhaul_entry(HUB_A, service),
                backhaul_entry(HUB_B, other),
            ]
        }
    )
    route.merge_patch({"backhaul_edges": {service: delete()}})
    assert route.backhaul_edges == [
        BackhaulEdge.model_validate(backhaul_entry(HUB_B, other))
    ]


def test_the_hub_lists_take_an_added_entry_by_name_key():
    vpn = SegmentVpn.model_validate(ds_with_vpn(GLOBAL, hubs=(HUB_A,))["vpn"])
    vpn.merge_patch({"hub_edges": {"vpn_hubs": {HUB_A.logical_id: delete()}}})
    assert vpn.hub_edges.vpn_hubs == []
    entry = hub_helpers.hub_entry(HUB_B)
    vpn.merge_patch({"hub_edges": {"vpn_hubs": {"+": add(entry)}}})
    assert [h.logical_id for h in vpn.hub_edges.vpn_hubs] == [
        uuid.UUID(HUB_B.logical_id)
    ]


def test_resolvable_attrs_exist():
    # The Resolvable checks run at class creation: importing the models above did not raise.
    for model, name, attribute in (
        (VpnHub, "type_", "type_"),
        (VpnHub, "logical_id", "logical_id"),
        (BackhaulEdge, "type_", "type_"),
        (BackhaulEdge, "edge_logical_id", "logical_id"),
        (BackhaulEdge, "edge_id", "edge_id"),
    ):
        resolvable = get_resolvable(model.model_fields[name])
        assert resolvable is not None
        assert (resolvable.identity_type, resolvable.attribute) == (
            HubIdentity,
            attribute,
        )
    assert get_name_of(VpnHub.model_fields["name"]) is HubIdentity
    assert get_name_of(BackhaulEdge.model_fields["edge_name"]) is HubIdentity
    assert get_resolvable(BackhaulEdge.model_fields["logical_id"]) is None
    assert [f.name for f in dataclasses.fields(HubIdentity)] == [
        "name",
        "kind",
        "type_",
        "logical_id",
        "edge_id",
        "edge_logical_id",
        "cluster_id",
        "cluster_logical_id",
    ]
    assert [f.name for f in dataclasses.fields(HubService)] == [
        "object_id",
        "logical_id",
        "data_logical_id",
        "segment_object_id",
        "kind",
        "name",
    ]
    assert HubName.identity_type is HubIdentity
    assert HubLogicalId.identity_type is HubIdentity


def test_the_markers_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        HubRole("hub").role = "bridge"  # pyright: ignore[reportAttributeAccessIssue]
