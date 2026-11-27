"""The hub services of a profile.

Also the backhaul entries that name a hub with no service.

A QoS rule's backhaul entry names a hub twice: ``edgeLogicalId`` is the hub's
own logical ID, and ``logicalId`` the hub's service for the profile and the
segment the rule is in. The ref row of the rule (``deviceSettings:backHaulEdge``)
points at that service, which only a role call makes: it is the row the profile's
device settings module holds for the hub (``deviceSettings:vpn:edgeHub`` for an
Edge, ``deviceSettings:vpn:edgeHubCluster`` for a cluster).
``HubServiceTable`` reads those rows, so the derivation of a QoS module's rows
(``pyvelo.client.derived_refs``) finds the service a new entry points at.
"""

import functools
import uuid
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import MISSING
from pydantic.fields import FieldInfo

from pyvelo.client.models.configuration.device_settings.segment import DeviceSegment
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge
from pyvelo.client.models.configuration.ref_owners import can_hold_owner, rows_named
from pyvelo.core import VcoModel
from pyvelo.core.base import (
    IdentityLookup,
    field_models,
    is_dict,
    list_kind,
    wire_name,
)
from pyvelo.core.fields import get_hub_role, get_owns_ref
from pyvelo.core.identity import (
    HubService,
    HubServiceNotFoundError,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.rebase import Path
from pyvelo.core.show import patch_text
from pyvelo.core.walk import can_hold_field, walk_fields

BACKHAUL_REF: Final = "deviceSettings:backHaulEdge"
"""The ref name of a QoS rule's backhaul hub row."""
EDGE_HUB_REF: Final = "deviceSettings:vpn:edgeHub"
"""The ref name of an Edge hub's row in the profile's device settings."""
CLUSTER_HUB_REF: Final = "deviceSettings:vpn:edgeHubCluster"
"""The ref name of a cluster's row in the profile's device settings."""


@functools.cache
def can_hold_hub_role(model_cls: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* can hold a field marked ``HubRole``, at any depth.

    True when one of its fields carries the marker, or the type of a field
    (through ``Annotated``, unions, ``list``, ``dict`` and ``tuple``) is a
    model class that can. It is computed from the declared field types, once
    per class.
    """
    return can_hold_field(model_cls, lambda info: get_hub_role(info) is not None)


@dataclass(frozen=True)
class HubListField:
    """A list of a device settings segment entry that holds the hubs of one role."""

    role: Literal["hub", "bridge", "backhaul"]
    path: tuple[str, ...]
    """The wire keys from the segment entry to the list:
    ``("vpn", "backHaulEdges")``. The raw JSON is read at them."""
    python_path: tuple[str, ...]
    """The attribute names from the segment entry to the list:
    ``("vpn", "backhaul_edges")``. A message and a patch name the list by them."""


@functools.cache
def hub_list_fields() -> tuple[HubListField, ...]:
    """The fields of a device settings segment entry marked ``HubRole``.

    Each with its role, its wire path and its path in attribute names, in
    field order.

    This is the one declaration of which lists hold hubs; the planner of the
    role steps walks the models for the same marker, and the one-module
    refusal reads the raw JSON at these paths. A marked field below a list or a
    map is not found: the lists of a segment's ``vpn`` are below plain models.
    """
    found: list[HubListField] = []

    def visit(
        model_cls: type[VcoModel], prefix: tuple[str, ...], names: tuple[str, ...]
    ) -> None:
        for name, field_info in model_cls.model_fields.items():
            here = (*prefix, wire_name(model_cls, name))
            named = (*names, name)
            role = get_hub_role(field_info)
            if role is not None:
                found.append(HubListField(role.role, here, named))
            elif list_kind(field_info) is None:
                for below in field_models(field_info):
                    visit(below, here, named)

    visit(DeviceSegment, (), ())
    return tuple(found)


def _uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _service(
    row: dict[object, object], kind: Literal["edge", "cluster"]
) -> HubService | None:
    """The service a hub row of *kind* stands for.

    ``None`` for a row that does not say.
    """
    object_id = row.get("enterpriseObjectId")
    logical_id = _uuid(row.get("logicalId"))
    if not isinstance(object_id, int) or logical_id is None:
        return None
    segment_object_id = row.get("segmentObjectId")
    held = row.get("data")
    data_logical_id = logical_id
    if kind == "edge":
        data_logical_id = _uuid(held.get("logicalId")) if is_dict(held) else None
    name = row.get("name")
    return HubService(
        object_id=object_id,
        logical_id=logical_id,
        data_logical_id=data_logical_id,
        segment_object_id=segment_object_id
        if isinstance(segment_object_id, int)
        else None,
        kind=kind,
        name=name if isinstance(name, str) else "",
    )


class HubServiceTable:
    """The hub services of a profile's device settings module, by segment."""

    _services: tuple[HubService, ...]

    def __init__(self, services: tuple[HubService, ...]) -> None:
        """A table of *services*; ``from_refs`` builds one from a module's refs."""
        self._services = services

    @classmethod
    def from_refs(cls, raw_refs: object) -> Self:
        """The services the hub rows of *raw_refs* stand for, in read order.

        An Edge hub's row gives the service's ``object_id`` (its
        ``enterpriseObjectId``), its ``logical_id`` (the row's) and the Edge's
        logical ID (``data.logicalId``). A cluster's row gives the cluster:
        its ``logical_id`` is the cluster's own, and so is its
        ``data_logical_id``. A cluster's one object serves every segment, so a
        service is found by its segment too. A row with no usable ``logicalId``
        or ``enterpriseObjectId`` is left out; ``MISSING`` holds none.
        """
        found: list[HubService] = []
        kinds: tuple[tuple[str, Literal["edge", "cluster"]], ...] = (
            (EDGE_HUB_REF, "edge"),
            (CLUSTER_HUB_REF, "cluster"),
        )
        for name, kind in kinds:
            for row in rows_named(raw_refs, name):
                service = _service(row, kind)
                if service is not None:
                    found.append(service)
        return cls(tuple(found))

    def services(self) -> tuple[HubService, ...]:
        """Every service of the table.

        Edge hubs first, then clusters, each in read order.
        """
        return self._services

    def find(
        self, segment_object_id: int, service_logical_id: uuid.UUID
    ) -> HubService | None:
        """The service of segment *segment_object_id* named by *service_logical_id*.

        That is the one whose ``logical_id`` is *service_logical_id*; else
        ``None``.

        The segment is part of the key because a cluster's one object stands
        for the cluster in every segment.
        """
        return next(
            (
                s
                for s in self._services
                if s.segment_object_id == segment_object_id
                and s.logical_id == service_logical_id
            ),
            None,
        )

    def find_for_hub(
        self, segment_object_id: int, hub_logical_id: uuid.UUID
    ) -> HubService | None:
        """The service of segment *segment_object_id* for a hub, else ``None``.

        The hub is the one with logical ID *hub_logical_id*.

        The hub's logical ID is the service's ``data_logical_id``: the Edge's
        for an Edge hub, the cluster's own for a cluster.
        """
        return next(
            (
                s
                for s in self._services
                if s.segment_object_id == segment_object_id
                and s.data_logical_id == hub_logical_id
            ),
            None,
        )


def _set(value: object) -> bool:
    """Whether *value* names something.

    ``MISSING``, ``None`` and ``""`` name nothing.
    """
    return value is not MISSING and value is not None and value != ""


def hub_name(entry: object) -> str:
    """The name of the hub a ``BackhaulEdge`` *entry* names, else its logical ID."""
    name: object = getattr(entry, "edge_name", None)
    if isinstance(name, str) and name:
        return name
    return str(getattr(entry, "edge_logical_id", None))


def _is_backhaul_service(field_info: FieldInfo) -> bool:
    owns = get_owns_ref(field_info)
    return owns is not None and owns.name == BACKHAUL_REF


def _unserved(data: VcoModel) -> Iterator[tuple[Path, BackhaulEdge, VcoModel | None]]:
    """Each entry of *data* that names a hub and no service.

    Each comes with its path and its segment entry.
    """
    for site in walk_fields(data, holds=can_hold_owner, fields=_is_backhaul_service):
        model = site.model
        if (
            isinstance(model, BackhaulEdge)
            and _set(model.edge_logical_id)
            and not _set(model.logical_id)
        ):
            yield Path(site.path.parts[:-1]), model, site.segment


def missing_services(data: VcoModel) -> tuple[Path, ...]:
    """The paths of the ``BackhaulEdge`` entries of *data* with a hub and no service.

    Each names a hub (``edge_logical_id``) and no service (``logical_id``).

    Such an entry cannot have its row: the row points at the service.
    """
    return tuple(path for path, _, _ in _unserved(data))


def _segment_name(segment: VcoModel | None) -> str:
    metadata: object = getattr(segment, "segment", None)
    name: object = getattr(metadata, "name", None)
    return name if isinstance(name, str) else ""


def _segment_object_id(
    segment: VcoModel | None,
    segment_object_ids: Mapping[int, int],
    resolvers: IdentityLookup | None,
) -> int | None:
    """The object ID of the segment entry *segment*.

    It comes from *segment_object_ids*, else from the segment resolver;
    ``None`` when there is none.

    Raises ``ResolverNotLoadedError`` when the resolver is needed and not loaded.
    """
    metadata: object = getattr(segment, "segment", None)
    segment_id: object = getattr(metadata, "segment_id", None)
    if not isinstance(segment_id, int):
        return None
    found = segment_object_ids.get(segment_id)
    if found is not None or resolvers is None:
        return found
    try:
        identity = resolvers.lookup_by(
            SegmentIdentity, lambda s: s.segment_id, segment_id
        )
    except ResolverNotLoadedError:
        raise
    except ResolutionError:
        return None
    return identity.object_id


def fill_services(
    data: VcoModel,
    table: HubServiceTable,
    segment_object_ids: Mapping[int, int],
    resolvers: IdentityLookup | None,
    *,
    skip: Collection[Path] = (),
) -> None:
    """Give each ``BackhaulEdge`` of *data* the hub's service for its segment.

    Each entry given one names a hub and no service.

    The service is the one *table* holds for the hub (``edge_logical_id``)
    in the entry's segment, found by the segment's object ID: *segment_object_ids*
    (``segmentId`` to object ID) first, else the segment resolver of *resolvers*.
    An entry at a path of *skip* is left as it is (one that lacked its
    service as read, which an edit that does not touch it is no cause to
    refuse). Raises ``HubServiceNotFoundError`` for the first entry whose hub
    has no service in its segment, before any entry is changed, and
    ``ResolverNotLoadedError`` when a segment's object ID needs the segment
    resolver and it is not loaded.
    """
    found: list[tuple[BackhaulEdge, uuid.UUID]] = []
    for path, entry, segment in _unserved(data):
        if path in skip:
            continue
        object_id = _segment_object_id(segment, segment_object_ids, resolvers)
        hub = entry.edge_logical_id
        service = (
            table.find_for_hub(object_id, hub)
            if object_id is not None and isinstance(hub, uuid.UUID)
            else None
        )
        if service is None:
            raise HubServiceNotFoundError(
                hub=hub_name(entry), segment=_segment_name(segment), path=path
            )
        found.append((entry, service.logical_id))
    for entry, logical_id in found:
        entry.logical_id = logical_id


def missing_service_error(
    data: VcoModel, *, as_read: VcoModel | None = None
) -> HubServiceNotFoundError | None:
    """The error for the first entry of *data* that names a hub and no service.

    ``None`` when there is none.

    An entry that was so as *as_read* is left out: the module as it stands
    was stored with it, and an edit that does not touch it is no cause to
    refuse. The message says to give the entry its ``logicalId``, or to add
    the hub through ``apply``, which fills it.
    """
    known: frozenset[Path] | None = None
    for path, entry, segment in _unserved(data):
        if known is None:  # as read is walked only when there is something to compare
            known = (
                frozenset() if as_read is None else frozenset(missing_services(as_read))
            )
        if path in known:
            continue
        hub = hub_name(entry)
        metadata: object = getattr(segment, "segment", None)
        name: object = getattr(metadata, "name", None)
        return HubServiceNotFoundError(
            hub=hub,
            segment=name if isinstance(name, str) else "",
            path=path,
            message=(
                f"{path} names the hub {hub!r} and no service for it: the row "
                "that goes with the rule points at the hub's service for this "
                "profile and segment, so it cannot be added. Give the entry's "
                "logicalId, the hub's service: "
                f"{patch_text(path.child('logical_id'))}, "
                "or add the hub through apply, which fills it."
            ),
        )
    return None
