"""The hub services of a profile, and the backhaul entries that name a hub with no service.

A QoS rule's backhaul entry names a hub twice: ``edgeLogicalId`` is the hub's
own logical ID, and ``logicalId`` the hub's service for the profile and the
segment the rule is in. The ref row of the rule (``deviceSettings:backHaulEdge``)
points at that service, which only a role call makes: it is the row the profile's
device settings module holds for the hub (``deviceSettings:vpn:edgeHub`` for an
Edge, ``deviceSettings:vpn:edgeHubCluster`` for a cluster).
``HubServiceTable`` reads those rows, so the derivation of a QoS module's rows
(``pyvelo.client.derived_refs``) finds the service a new entry points at.
"""

import uuid
from collections.abc import Iterator
from typing import Final, Literal, Self

from pydantic import MISSING

from pyvelo.client.models.configuration.qos.rule import BackhaulEdge
from pyvelo.client.models.configuration.ref_owners import can_hold_owner, rows_named
from pyvelo.core import VcoModel
from pyvelo.core.base import is_dict
from pyvelo.core.identity import HubService, HubServiceNotFoundError
from pyvelo.core.rebase import Path
from pyvelo.core.show import patch_text
from pyvelo.core.walk import walk_fields

BACKHAUL_REF: Final = "deviceSettings:backHaulEdge"
"""The ref name of a QoS rule's backhaul hub row."""
EDGE_HUB_REF: Final = "deviceSettings:vpn:edgeHub"
"""The ref name of an Edge hub's row in the profile's device settings."""
CLUSTER_HUB_REF: Final = "deviceSettings:vpn:edgeHubCluster"
"""The ref name of a cluster's row in the profile's device settings."""


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
    """The service a hub row of *kind* stands for, or ``None`` for a row that does not say."""
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
        """Every service of the table: Edge hubs first, then clusters, each in read order."""
        return self._services

    def find(
        self, segment_object_id: int, service_logical_id: uuid.UUID
    ) -> HubService | None:
        """The service of segment *segment_object_id* whose ``logical_id`` is *service_logical_id*, else ``None``.

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
        """The service of segment *segment_object_id* for the hub with logical ID *hub_logical_id*, else ``None``.

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
    """Whether *value* names something: ``MISSING``, ``None`` and ``""`` name nothing."""
    return value is not MISSING and value is not None and value != ""


def hub_name(entry: object) -> str:
    """The name of the hub a ``BackhaulEdge`` *entry* names, else its logical ID."""
    name: object = getattr(entry, "edge_name", None)
    if isinstance(name, str) and name:
        return name
    return str(getattr(entry, "edge_logical_id", None))


def _unserved(data: VcoModel) -> Iterator[tuple[Path, BackhaulEdge, VcoModel | None]]:
    """Each entry of *data* that names a hub and no service, with its path and its segment entry."""
    for site in walk_fields(data, holds=can_hold_owner):
        model = site.model
        if (
            isinstance(model, BackhaulEdge)
            and site.name == "logical_id"
            and _set(model.edge_logical_id)
            and not _set(model.logical_id)
        ):
            yield Path(site.path.parts[:-1]), model, site.segment


def missing_services(data: VcoModel) -> tuple[Path, ...]:
    """The paths of the ``BackhaulEdge`` entries of *data* that name a hub (``edge_logical_id``) and no service (``logical_id``).

    Such an entry cannot have its row: the row points at the service.
    """
    return tuple(path for path, _, _ in _unserved(data))


def missing_service_error(
    data: VcoModel, *, as_read: VcoModel | None = None
) -> HubServiceNotFoundError | None:
    """The error for the first entry of *data* that names a hub and no service, or ``None`` when there is none.

    An entry that was so as *as_read* is left out: the module as it stands
    was stored with it, and an edit that does not touch it is no cause to
    refuse. The message says to give the entry its ``logicalId``, or to add
    the hub through ``apply``, which fills it.
    """
    known: frozenset[Path] = (
        frozenset() if as_read is None else frozenset(missing_services(as_read))
    )
    for path, entry, segment in _unserved(data):
        if path in known:
            continue
        hub = hub_name(entry)
        metadata: object = getattr(segment, "segment", None)
        name: object = getattr(metadata, "name", None)
        return HubServiceNotFoundError(
            hub=hub,
            segment=name if isinstance(name, str) else "",
            path=str(path),
            message=(
                f"{path} names the hub {hub!r} and no service for it: the row "
                + "that goes with the rule points at the hub's service for this "
                + "profile and segment, so it cannot be added. Give the entry's "
                + f"logicalId, the hub's service: {patch_text(path.child('logical_id'))}, "
                + "or add the hub through apply, which fills it."
            ),
        )
    return None
