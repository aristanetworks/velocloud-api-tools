"""The role steps of a save: a hub added to or removed from a segment's ``vpn`` hub lists.

A hub in the hub, bridge or backhaul list of a profile segment works only when
its service has the role, which the VCO's role calls set (the UI's enable and
disable calls). A write of the device settings module cannot do that, so a save
that changes the lists also sends one step per hub and list, after the module
write, in the UI's order. ``derive_steps`` plans them from the module as read
and as edited, and ``role_disagreements`` warns of a hub whose roles as read
do not match the lists it is in.
"""

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from pyvelo.client.hub_services import (
    CLUSTER_HUB_REF,
    EDGE_HUB_REF,
    can_hold_hub_role,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    VpnHub,
)
from pyvelo.client.models.configuration.ref_owners import (
    SegmentRef,
    row_is_of_segment,
    rows_named,
)
from pyvelo.client.models.configuration.ref_owners import (
    segment_object_ids as object_ids_of,
)
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.fields import get_hub_role
from pyvelo.core.identity import (
    HubIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.walk import walk_fields

from pyvelo_intent.config.validation_warning import ValidationWarning

type Role = Literal["hub", "bridge", "backhaul"]
type Action = Literal["enable", "disable"]

_ROLES: Final[tuple[Role, ...]] = ("hub", "bridge", "backhaul")
_ROLE_FLAGS: Final[dict[Role, str]] = {
    "hub": "edgeHub",
    "bridge": "edgeToEdgeBridge",
    "backhaul": "backHaulEdge",
}
"""The key of each role in the ``roles`` of a hub's service."""
_ORDER: Final[tuple[tuple[Action, Role], ...]] = (
    ("disable", "backhaul"),
    ("disable", "bridge"),
    ("disable", "hub"),
    ("enable", "hub"),
    ("enable", "backhaul"),
    ("enable", "bridge"),
)
"""The order of the UI's role calls: every disable before any enable, and a role after the hub role it needs."""


@dataclass(frozen=True)
class HubRef:
    """The Edge or the cluster a step is for.

    *edge_id* (an Edge) or *cluster_id* (a cluster) is None for a hub being
    removed: the save finds it from the services it reads before the first
    step, so planning a removal needs no hub resolver.
    """

    kind: Literal["edge", "cluster"]
    logical_id: uuid.UUID
    name: str
    edge_id: int | None
    cluster_id: int | None


@dataclass(frozen=True)
class HubStep:
    """One role call of a save: *action* the *role* of *hub* for a segment.

    *segment_object_id* is the segment's object, which the role calls take.
    """

    action: Action
    role: Role
    hub: HubRef
    segment: str
    segment_id: int
    segment_object_id: int

    @property
    def gives(self) -> Literal["service"] | None:
        """What the call makes: ``"service"`` for an enable (the hub's service for the segment), else None."""
        return "service" if self.action == "enable" else None

    def render(self) -> str:
        """The step as a line: ``enable backhaul for Hub-A on segment Global Segment`` (``for cluster Cluster-1`` for a cluster)."""
        who = (
            f"cluster {self.hub.name}" if self.hub.kind == "cluster" else self.hub.name
        )
        return f"{self.action} {self.role} for {who} on segment {self.segment}"


type _Key = tuple[str, uuid.UUID]


def _key(hub: VpnHub) -> _Key:
    return (hub.type_, hub.logical_id)


def _lists(data: DeviceSettingsData) -> dict[int, dict[Role, list[VpnHub]]]:
    """The hub lists of each segment of *data*, by segment ID and role: the fields marked ``HubRole``."""
    found: dict[int, dict[Role, list[VpnHub]]] = {}
    for site in walk_fields(
        data,
        holds=can_hold_hub_role,
        fields=lambda field_info: get_hub_role(field_info) is not None,
    ):
        role = get_hub_role(site.field_info)
        if role is None or not isinstance(site.segment, DeviceSegment):
            continue  # the walk gives only marked fields, and those outside a segment are not its
        held: object = getattr(site.model, site.name)
        hubs = [h for h in held if isinstance(h, VpnHub)] if is_list(held) else []
        found.setdefault(_segment_id(site.segment), {})[role.role] = hubs
    return found


def _segment_id(segment: DeviceSegment) -> int:
    return segment.segment.segment_id


@dataclass(frozen=True)
class _Difference:
    """The hubs one segment's list of one role gains and loses, in list order."""

    segment: DeviceSegment
    role: Role
    added: tuple[VpnHub, ...]
    removed: tuple[VpnHub, ...]


def _differences(
    as_read: DeviceSettingsData, now: DeviceSettingsData
) -> Iterator[_Difference]:
    """The changes of each hub list, segment by segment in the order of *now*, by ``(type, logical ID)``."""
    lists_read, lists_now = _lists(as_read), _lists(now)
    for segment in now.segments:
        was = lists_read.get(_segment_id(segment), {})
        for role, edited in lists_now.get(_segment_id(segment), {}).items():
            read = was.get(role, [])
            kept = {_key(h) for h in edited}
            held = {_key(h) for h in read}
            added = tuple(h for h in edited if _key(h) not in held)
            removed = tuple(h for h in read if _key(h) not in kept)
            if added or removed:
                yield _Difference(segment, role, added, removed)


def changed_segment_ids(
    as_read: DeviceSettingsData, now: DeviceSettingsData
) -> tuple[int, ...]:
    """The IDs of the segments whose hub lists change, in the order of *now*: the segments ``derive_steps`` needs an object ID for."""
    return tuple(
        dict.fromkeys(_segment_id(d.segment) for d in _differences(as_read, now))
    )


class _AddedHubs:
    """The identity of each hub the edit adds, found by logical ID in a loaded ``HubResolver``."""

    def __init__(self, hubs: HubResolver | None) -> None:
        self._hubs = hubs
        self._found: dict[uuid.UUID, HubIdentity] | None = None

    def ref(self, hub: VpnHub) -> HubRef:
        """The ``HubRef`` of *hub*, with its Edge or cluster ID. Raises ``ResolverNotLoadedError`` when the resolver is None or not loaded."""
        if self._found is None:
            if self._hubs is None or not self._hubs.loaded:
                raise ResolverNotLoadedError(HubIdentity)
            self._found = {i.logical_id: i for i in self._hubs.identities()}
        identity = self._found.get(hub.logical_id)
        if identity is None or identity.type_ != hub.type_:
            raise ResolutionError(
                name=str(hub.logical_id),
                identity_type=HubIdentity,
                candidates=[],
                message=(
                    f"No Edge or hub cluster of the enterprise has the logical ID "
                    f"{hub.logical_id} ({hub.name!r}), so its role cannot be set. "
                    "A hub cluster's member is no hub of its own: give the "
                    "cluster."
                ),
            )
        return HubRef(
            identity.kind,
            identity.logical_id,
            identity.name,
            identity.edge_id,
            identity.cluster_id,
        )


def _removed_ref(hub: VpnHub) -> HubRef:
    return HubRef(
        "edge" if hub.type_ == "edgeHub" else "cluster",
        hub.logical_id,
        hub.name,
        None,
        None,
    )


def derive_steps(
    as_read: DeviceSettingsData,
    now: DeviceSettingsData,
    *,
    segment_object_ids: Mapping[int, int],
    hubs: HubResolver | None,
) -> tuple[HubStep, ...]:
    """The role steps that make the hub lists of *as_read* those of *now*, in the UI's order.

    Segments are matched by ``segment_id``, and a hub by ``(type, logical ID)``
    of its list entry, so a reorder is no step. The order is: disable backhaul,
    disable bridge, disable hub, enable hub, enable backhaul, enable bridge;
    within one kind, the segments in the order of *now*, then the hubs in the
    order of their list. A hub removed from a segment's hub list gets no
    backhaul or bridge disable there: disabling the hub clears its roles.

    *segment_object_ids* is each segment's object ID by ``segment_id``
    (``changed_segment_ids`` says which segments need one); a segment without
    raises ``ResolutionError``. An added hub is looked up by logical ID in
    *hubs*, which must be loaded: ``ResolverNotLoadedError(HubIdentity)`` when
    it is None or not. A removal needs no resolver.
    """
    differences = list(_differences(as_read, now))
    unhubbed: set[tuple[int, _Key]] = {
        (_segment_id(d.segment), _key(h))
        for d in differences
        if d.role == "hub"
        for h in d.removed
    }
    added_hubs = _AddedHubs(hubs)
    planned: dict[tuple[Action, Role], list[HubStep]] = {kind: [] for kind in _ORDER}
    for difference in differences:
        segment = difference.segment
        segment_id = _segment_id(segment)
        object_id = segment_object_ids.get(segment_id)
        if object_id is None:
            raise ResolutionError(
                name=segment.segment.name,
                identity_type=SegmentIdentity,
                candidates=[],
                message=(
                    f"Segment {segment.segment.name!r} (ID {segment_id}) has no "
                    "object ID, which the role calls need: its "
                    "`deviceSettings:segment` row is not in the profile's device "
                    "settings and the segment resolver does not know it."
                ),
            )
        for hub in difference.removed:
            if difference.role != "hub" and (segment_id, _key(hub)) in unhubbed:
                continue
            planned["disable", difference.role].append(
                HubStep(
                    "disable",
                    difference.role,
                    _removed_ref(hub),
                    segment.segment.name,
                    segment_id,
                    object_id,
                )
            )
        for hub in difference.added:
            planned["enable", difference.role].append(
                HubStep(
                    "enable",
                    difference.role,
                    added_hubs.ref(hub),
                    segment.segment.name,
                    segment_id,
                    object_id,
                )
            )
    return tuple(step for kind in _ORDER for step in planned[kind])


# --- roles that disagree with the lists ------------------------------------------


def _cluster_roles(
    data: object, configuration_id: int, segment_id: int | None
) -> object:
    """The ``roles`` a cluster's row data holds for a segment of the profile, or None.

    A ref row's ``data`` is the enterprise object's own: for a cluster, its
    roles by configuration ID and then segment ID.
    """
    held = data.get(str(configuration_id)) if is_dict(data) else None
    entry = held.get(str(segment_id)) if is_dict(held) else None
    return entry.get("roles") if is_dict(entry) else None


def _hub_roles(
    hub: VpnHub,
    segment: SegmentRef,
    raw_refs: object,
    configuration_id: int,
    object_ids: dict[int, int],
) -> dict[object, object] | None:
    """The roles the as-read row of *hub* for *segment* holds, or None when it has no row."""
    if hub.type_ == "edgeHubCluster":
        for row in rows_named(raw_refs, CLUSTER_HUB_REF):
            if row.get("configurationId") != configuration_id:
                continue
            if str(row.get("logicalId")).lower() != str(hub.logical_id):
                continue
            roles = _cluster_roles(
                row.get("data"), configuration_id, segment.segment_id
            )
            if is_dict(roles):
                return roles
        return None
    for row in rows_named(raw_refs, EDGE_HUB_REF):
        data = row.get("data")
        if (
            row.get("configurationId") == configuration_id
            and is_dict(data)
            and str(data.get("logicalId")).lower() == str(hub.logical_id)
            and row_is_of_segment(row, segment, object_ids)
        ):
            roles = data.get("roles")
            return roles if is_dict(roles) else {}
    return None


def role_disagreements(
    as_read: DeviceSettingsData, raw_refs: object, configuration_id: int
) -> tuple[ValidationWarning, ...]:
    """A ``hub_roles_disagree`` warning for each hub of a segment whose as-read roles do not match the lists it is in.

    *raw_refs* are the refs of the profile's device settings module as read
    and *configuration_id* the profile's. A hub's row says (``roles``) whether
    its service has the hub, bridge and backhaul roles for the segment; the
    lists say where the segment uses it. A bridge or backhaul list that holds a
    hub whose service lacks that role is a hub no Edge can use, and a service
    with the role that the list does not hold a role that does nothing (the hub
    role is implied by the other two, so it is checked only for a hub in the
    hub list). A hub with no row is not reported here.
    """
    object_ids = object_ids_of(raw_refs)
    lists_of_segments = _lists(as_read)
    warnings: list[ValidationWarning] = []
    for segment in as_read.segments:
        descriptor = segment.segment
        ref = SegmentRef(
            descriptor.segment_id, descriptor.name, descriptor.segment_logical_id
        )
        lists = lists_of_segments.get(descriptor.segment_id, {})
        members = {role: {_key(h) for h in hubs} for role, hubs in lists.items()}
        seen: dict[_Key, VpnHub] = {}
        for role in _ROLES:
            for hub in lists.get(role, []):
                seen.setdefault(_key(hub), hub)
        for key, hub in seen.items():
            roles = _hub_roles(hub, ref, raw_refs, configuration_id, object_ids)
            if roles is None:
                continue
            problems: list[str] = []
            for role in _ROLES:
                listed = key in members.get(role, set())
                has = roles.get(_ROLE_FLAGS[role]) is True
                if listed and not has:
                    problems.append(
                        f"the {role} list holds it, but its service lacks the "
                        f"{role} role"
                    )
                elif has and not listed and role != "hub":
                    problems.append(
                        f"its service has the {role} role, but the {role} list "
                        "does not hold it"
                    )
            if problems:
                warnings.append(
                    ValidationWarning(
                        path=f'deviceSettings: segments["{descriptor.name}"].vpn[{hub.name}]',
                        message=(
                            f"hub {hub.name!r} in segment {descriptor.name!r}: "
                            + "; ".join(problems)
                            + " (it was so as read; a save sets roles only for "
                            "the hubs the edit adds or removes)"
                        ),
                        code="hub_roles_disagree",
                    )
                )
    return tuple(warnings)
