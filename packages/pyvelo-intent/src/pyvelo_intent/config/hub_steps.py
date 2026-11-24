"""The role steps of a save: a hub added to or removed from a segment's ``vpn`` hub lists.

A hub in the hub, bridge or backhaul list of a profile segment works only when
its service has the role, which the VCO's role calls set (the UI's enable and
disable calls). A write of the device settings module cannot do that, so a save
that changes the lists also sends one step per hub and list, before the module
write, in the UI's order. ``derive_steps`` plans them from the module as read
and as edited, and ``role_disagreements`` warns of a hub whose roles as read
do not match the lists it is in. ``check_hub_lists`` refuses, before any call,
a list edit that breaks the UI's rules for the lists. The save reads the hub
services (``read_services``), sends each step not already in place
(``in_place``, ``send_step``) and writes the rows the steps made
(``hub_rows``).
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

from pydantic import MISSING, ValidationError
from pyvelo.client.enums.enterprise import (
    EnterpriseServicesParamsWith,
    EnterpriseServiceType,
)
from pyvelo.client.exceptions import HubListError
from pyvelo.client.hub_services import (
    CLUSTER_HUB_REF,
    EDGE_HUB_REF,
    can_hold_hub_role,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentVpn,
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
from pyvelo.client.models.enterprise.service_data import EdgeRoles
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.edge import (
    DisableEdgeBackHaul,
    DisableEdgeHub,
    DisableEdgeToEdgeBridge,
    EnableEdgeBackHaul,
    EnableEdgeHub,
    EnableEdgeToEdgeBridge,
)
from pyvelo.client.schemas.enterprise import (
    DisableClusterBackHaul,
    DisableClusterHub,
    DisableClusterToEdgeBridge,
    EdgeHubClusterService,
    EdgeHubService,
    EnableClusterBackHaul,
    EnableClusterHub,
    EnableClusterToEdgeBridge,
    GetEnterpriseServices,
)
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.fields import get_hub_role
from pyvelo.core.identity import (
    HubIdentity,
    HubServiceNotFoundError,
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


# --- the UI's rules for the lists ------------------------------------------------

_LIST_PATHS: Final[dict[Role, tuple[str, ...]]] = {
    "hub": ("vpn", "edgeToEdgeHub", "vpnHubs"),
    "bridge": ("vpn", "edgeToEdgeDetail", "vpnHubs"),
    "backhaul": ("vpn", "backHaulEdges"),
}
"""The wire path of each role's list, from a segment."""
_LIST_LABELS: Final[dict[Role, str]] = {
    "hub": "Branch-to-Hub list",
    "bridge": "bridge list",
    "backhaul": "backhaul list",
}
_APPLY: Final = '(`pipeline.apply(state, module="deviceSettings", patch=...)`)'


def _patch(segment: str, path: tuple[str, ...], change: object) -> str:
    """The patch text of a ``deviceSettings`` edit that sets *change* at *path* of *segment*."""
    node: object = change
    for key in reversed(path):
        node = {key: node}
    return json.dumps({"segments": {segment: node}})


def _list_text(role: Role) -> str:
    """How a message names the list of *role*: its label and its wire path."""
    return f"the {_LIST_LABELS[role]} (`{'.'.join(_LIST_PATHS[role])}`)"


def _removal(segment: str, role: Role, hubs: Sequence[VpnHub]) -> str:
    """The patch that removes *hubs* from the list of *role* of *segment*, with the helper beside it."""
    change = {h.name: {"_op": "delete"} for h in hubs}
    return (
        f"{_patch(segment, _LIST_PATHS[role], change)} (`delete()` in Python) {_APPLY}"
    )


def _addition(segment: str, role: Role, hub_name: str) -> str:
    """The patch that adds the hub named *hub_name* to the list of *role* of *segment*."""
    change = {"+": {"_op": "add", "name": hub_name}}
    return f"{_patch(segment, _LIST_PATHS[role], change)} {_APPLY}"


def _flag(segment: str, path: tuple[str, ...], value: bool) -> str:
    """The patch that sets the ``vpn`` flag at *path* of *segment*."""
    return f"{_patch(segment, path, value)} {_APPLY}"


def _names(hubs: Sequence[VpnHub]) -> str:
    return ", ".join(repr(h.name) for h in hubs)


@dataclass(frozen=True)
class _Problem:
    """One rule a segment's lists break.

    *key* says how: the same key as read is the same rule broken the same way.
    A rule that is broken by the hubs a list holds (the list is off while it
    has hubs) also has them in *listed*, each with the role of the list that
    holds it: it is the same break as read only when every hub listed now was
    listed in that list as read, so an edit that adds a hub to any list of a
    segment already broken is a new break.
    """

    key: tuple[object, ...]
    message: str
    lists: tuple[str, ...]
    hubs: tuple[str, ...]
    listed: frozenset[tuple[Role, _Key]] = frozenset()


def _problem(
    key: tuple[object, ...],
    message: str,
    roles: Sequence[Role],
    hubs: Sequence[VpnHub] = (),
    *,
    listed: Sequence[tuple[Role, VpnHub]] = (),
) -> _Problem:
    return _Problem(
        key,
        message,
        tuple(".".join(_LIST_PATHS[r]) for r in roles),
        tuple(dict.fromkeys(h.name for h in hubs)),
        frozenset((role, _key(h)) for role, h in listed),
    )


def _broken_rules(
    segment: DeviceSegment,
    lists: Mapping[Role, list[VpnHub]],
    read: SegmentVpn | None,
) -> list[_Problem]:
    """The rules about the lists and flags that *segment* breaks, as they stand.

    *read* is the segment's ``vpn`` as read, or None: a flag turned off since
    gets the message that says to remove the hubs first.
    """
    name = segment.segment.name
    vpn = segment.vpn
    hub_list = lists.get("hub", [])
    bridges = lists.get("bridge", [])
    problems: list[_Problem] = []
    prefix = f"deviceSettings: segment {name!r}: "

    # Rule 2: no logical ID twice in one list.
    for role in _ROLES:
        seen: set[_Key] = set()
        for hub in lists.get(role, []):
            if _key(hub) in seen:
                problems.append(
                    _problem(
                        (2, role, _key(hub)),
                        f"{prefix}{_list_text(role)} holds {hub.name!r} twice. The "
                        "VCO keeps the list as given, so the hub would get its "
                        "role set once for two entries and cleared once for two. "
                        f"List each hub once: remove it with {_removal(name, role, [hub])}"
                        f", then add it back once with {_addition(name, role, hub.name)}.",
                        [role],
                        [hub],
                    )
                )
            seen.add(_key(hub))

    # Rule 1: the Branch-to-Hub list is non-empty if and only if it is enabled.
    enabled = vpn.hub_edges.enabled
    flag_path = ("vpn", "edgeToEdgeHub", "enabled")
    if hub_list and not enabled:
        if read is not None and read.hub_edges.enabled:
            how = (
                f"The edit turns `{'.'.join(flag_path)}` off while the "
                f"{_LIST_LABELS['hub']} still holds {_names(hub_list)}. The UI "
                "turns it off only with an empty list, and the VCO would "
                "keep hubs the segment no longer uses. Remove the hubs first: "
                f"{_removal(name, 'hub', hub_list)}, then turn it off."
            )
        else:
            how = (
                f"{_list_text('hub')} holds {_names(hub_list)} while "
                f"`{'.'.join(flag_path)}` is off, so the hubs would be listed "
                "and unused. Turn it on with "
                f"{_flag(name, flag_path, True)} or remove the hubs with "
                f"{_removal(name, 'hub', hub_list)}."
            )
        problems.append(
            _problem(
                (1, "filled"),
                prefix + how,
                ["hub"],
                hub_list,
                listed=[(role, h) for role in _ROLES for h in lists.get(role, [])],
            )
        )
    if enabled and not hub_list:
        problems.append(
            _problem(
                (1, "empty"),
                f"{prefix}`{'.'.join(flag_path)}` is on but {_list_text('hub')} "
                "is empty. The UI turns it on only with a hub, and the segment's "
                "Edges would be told to use no hub. Add a hub with "
                f"{_addition(name, 'hub', '<hub name>')}"
                f" or turn it off with {_flag(name, flag_path, False)}.",
                ["hub"],
            )
        )

    # Rule 3: Edge to Edge through hubs needs a hub to go through.
    through_hubs = vpn.edge_to_edge and not vpn.edge_to_edge_detail.use_cloud_gateway
    if through_hubs and not bridges:
        problems.append(
            _problem(
                (3,),
                f"{prefix}`vpn.edgeToEdge` is on and "
                "`vpn.edgeToEdgeDetail.useCloudGateway` is off, so its Edges "
                f"reach one another through the hubs of {_list_text('bridge')}"
                ", which is empty: they could not reach one another. Add a "
                f"hub with {_addition(name, 'bridge', '<hub name>')}"
                " or turn `useCloudGateway` on with "
                f"{_flag(name, ('vpn', 'edgeToEdgeDetail', 'useCloudGateway'), True)}.",
                ["bridge"],
            )
        )

    # Rule 4: a backhaul or bridge hub is a hub of the segment.
    hub_keys = {_key(h) for h in hub_list}
    for role in ("bridge", "backhaul"):
        for hub in lists.get(role, []):
            if _key(hub) not in hub_keys:
                problems.append(
                    _problem(
                        (4, role, _key(hub)),
                        f"{prefix}{_list_text(role)} holds {hub.name!r}, which "
                        f"{_list_text('hub')} does not. A hub works for backhaul "
                        "or bridging only as a hub of the segment, and the UI "
                        "offers only those, so no Edge could use it. Add it to "
                        f"the {_LIST_LABELS['hub']} with "
                        f"{_addition(name, 'hub', hub.name)} or remove it from the "
                        f"{_LIST_LABELS[role]} too with {_removal(name, role, [hub])}.",
                        ["hub", role],
                        [hub],
                    )
                )

    # Rule 5: a segment that uses hubs has its VPN on.
    filled: list[Role] = [role for role in _ROLES if lists.get(role)]
    if filled and not vpn.enabled:
        listed = [h for role in filled for h in lists[role]]
        if read is not None and read.enabled:
            how = (
                "The edit turns `vpn.enabled` off while its lists still hold "
                f"{_names(listed)}. Turning the VPN off while hubs are listed "
                "leaves hubs whose roles are set for a segment that does not "
                "use them. Remove the hubs first (from "
                + ", ".join(_list_text(r) for r in filled)
                + f"), for example {_removal(name, filled[0], lists[filled[0]])}, "
                "then turn it off."
            )
        else:
            how = (
                f"{' and '.join(_list_text(r) for r in filled)} hold "
                f"{_names(listed)} while `vpn.enabled` is off, so the hubs would "
                "be listed for a segment that does not use them. Turn it on with "
                f"{_flag(name, ('vpn', 'enabled'), True)} or remove the hubs, for "
                f"example {_removal(name, filled[0], lists[filled[0]])}."
            )
        problems.append(
            _problem(
                (5,),
                prefix + how,
                filled,
                listed,
                listed=[(role, h) for role in filled for h in lists[role]],
            )
        )
    return problems


def _hub_problems(
    segment: DeviceSegment,
    role: Role,
    added: Sequence[VpnHub],
    hubs: HubResolver,
) -> list[_Problem]:
    """The rule-6 problems of the hubs *added* to the list of *role* of *segment*, against the loaded *hubs*."""
    name = segment.segment.name
    prefix = f"deviceSettings: segment {name!r}: "
    problems: list[_Problem] = []
    identities = {i.logical_id: i for i in hubs.identities()}
    for hub in added:
        key: tuple[object, ...] = (6, role, _key(hub))
        identity = identities.get(hub.logical_id)
        entry = f"{_list_text(role)} entry {hub.name!r} ({hub.type_}, {hub.logical_id})"
        if hubs.is_cluster_member(hub.logical_id):
            cluster = hubs.member_name(hub.logical_id) or hub.name
            message = (
                f"{prefix}{entry} is a member of a hub cluster, and a member is "
                "no hub of its own: the VCO makes the cluster the hub, so the "
                "role call for it would fail or set a role nothing uses. Give "
                f"the cluster instead ({cluster!r} is a member), with "
                f"{_addition(name, role, '<cluster name>')}."
            )
        elif identity is None:
            message = (
                f"{prefix}{entry} is no Edge or hub cluster of the enterprise, "
                "so no role can be set on a service it names. Give the name of "
                f"an Edge or a hub cluster: {_addition(name, role, '<Edge or cluster name>')}."
            )
        elif identity.type_ != hub.type_:
            kind = "an Edge" if identity.kind == "edge" else "a hub cluster"
            message = (
                f"{prefix}{entry} has the type {hub.type_!r}, but that logical "
                f"ID is {kind}'s ({identity.name!r}), so the role call for it "
                "would go to the wrong endpoint. Add the hub by its name, which "
                f"fills the type: {_addition(name, role, identity.name)}."
            )
        elif identity.name.lower() != hub.name.lower():
            message = (
                f"{prefix}{entry} is named {hub.name!r}, but that logical ID is "
                f"that of {identity.name!r}, so the entry would show one hub and "
                "set the roles of another. Add the hub by its name, which fills "
                f"the logical ID: {_addition(name, role, identity.name)}."
            )
        else:
            continue
        problems.append(_problem(key, message, [role], [hub]))
    return problems


def check_hub_lists(
    as_read: DeviceSettingsData,
    now: DeviceSettingsData,
    *,
    hubs: HubResolver | None,
) -> tuple[ValidationWarning, ...]:
    """Refuse a hub list edit that breaks the UI's rules, before any call; warn of a rule broken as read.

    The segments checked are those whose ``vpn`` differs in value from the module as
    read (the lists and the flags among it), matched by ``segment_id``, and
    those the edit adds. That the segment exists in the profile holds by
    construction: the steps come from the profile's own segments. The rules:

    1. the Branch-to-Hub list is non-empty if and only if
       ``vpn.hub_edges.enabled``; turning that or ``vpn.enabled`` off while it
       lists hubs says to remove the hubs first;
    2. no logical ID twice in one list;
    3. with ``edge_to_edge`` on and ``use_cloud_gateway`` off, the bridge list
       is non-empty;
    4. every hub of the backhaul or bridge list is in the Branch-to-Hub list;
    5. a segment whose lists are non-empty has ``vpn.enabled`` true;
    6. an added Edge is no cluster member and every added hub is an identity
       of *hubs*, whose type and name are those its logical ID names (name
       compared without regard to case).

    A rule broken the same way as read is a ``hub_list_as_read`` warning: the
    save leaves it. Any other raises ``HubListError``, which names the
    segments, the lists and the hubs and says what to do instead; all the
    problems found are in one error. Rule 6 needs *hubs* only when hubs were
    added: ``ResolverNotLoadedError(HubIdentity)`` when it is None or not
    loaded.
    """
    lists_read, lists_now = _lists(as_read), _lists(now)
    read_by_id = {_segment_id(s): s for s in as_read.segments}
    changed = [
        segment
        for segment in now.segments
        if (was := read_by_id.get(_segment_id(segment))) is None
        or was.vpn.model_dump() != segment.vpn.model_dump()
    ]
    added_hubs: dict[tuple[int, Role], list[VpnHub]] = {}
    for difference in _differences(as_read, now):
        if difference.added:
            added_hubs.setdefault(
                (_segment_id(difference.segment), difference.role), []
            ).extend(difference.added)
    if added_hubs and (hubs is None or not hubs.loaded):
        raise ResolverNotLoadedError(HubIdentity)

    warnings: list[ValidationWarning] = []
    errors: list[_Problem] = []
    names: list[str] = []
    for segment in changed:
        segment_id = _segment_id(segment)
        name = segment.segment.name
        was = read_by_id.get(segment_id)
        broken_before: dict[tuple[object, ...], frozenset[tuple[Role, _Key]]] = {}
        if was is not None:
            for read_problem in _broken_rules(
                was, lists_read.get(segment_id, {}), None
            ):
                held = broken_before.get(read_problem.key, frozenset())
                broken_before[read_problem.key] = held | read_problem.listed
        found = _broken_rules(
            segment, lists_now.get(segment_id, {}), None if was is None else was.vpn
        )
        for role in _ROLES:
            if hubs is not None and (added := added_hubs.get((segment_id, role))):
                found.extend(_hub_problems(segment, role, added, hubs))
        for problem in found:
            if (
                problem.key in broken_before
                and problem.listed <= broken_before[problem.key]
            ):
                warnings.append(
                    ValidationWarning(
                        path=f'deviceSettings: segments["{name}"].vpn',
                        message=problem.message
                        + " (It was so as read, and the save leaves it.)",
                        code="hub_list_as_read",
                    )
                )
            else:
                errors.append(problem)
                names.append(name)
    if errors:
        segments = list(dict.fromkeys(names))
        raise HubListError(
            "\n".join(p.message for p in errors),
            segment=segments[0] if len(segments) == 1 else None,
            lists=tuple(dict.fromkeys(path for p in errors for path in p.lists)),
            hubs=tuple(dict.fromkeys(h for p in errors for h in p.hubs)),
        )
    return tuple(warnings)


# --- the save's calls: the services, the steps and the hub rows --------------------

_ROLE_FIELDS: Final[dict[Role, str]] = {
    "hub": "edge_hub",
    "bridge": "edge_to_edge_bridge",
    "backhaul": "back_haul_edge",
}
"""The attribute of ``EdgeRoles`` that holds each role."""


@dataclass(frozen=True)
class HubServices:
    """The hub services of one read of the enterprise: the Edge hubs (with the rows that point at them) and the clusters."""

    edge_hubs: tuple[EdgeHubService, ...] = ()
    clusters: tuple[EdgeHubClusterService, ...] = ()

    def edge_service(
        self,
        hub_logical_id: uuid.UUID,
        configuration_id: int,
        module_id: int,
        segment_object_id: int,
    ) -> EdgeHubService | None:
        """The service of the Edge with logical ID *hub_logical_id* for that profile, module and segment, or None.

        It is the service whose ``data.logical_id`` is the Edge's and which has
        a ``configuration`` row of that profile, module and segment. Raises
        ``HubListError`` when several do: a role call could act on any of
        them, and the VCO's own lookup takes one and only logs a warning.
        """
        found = [
            service
            for service in self.edge_hubs
            if service.data.logical_id == hub_logical_id
            and _has_configuration(
                service, configuration_id, module_id, segment_object_id
            )
        ]
        if len(found) > 1:
            ids = ", ".join(str(service.id_) for service in found)
            raise HubListError(
                f"The VCO holds {len(found)} Edge hub services (IDs {ids}) for "
                f"the Edge with logical ID {hub_logical_id} in segment object "
                f"{segment_object_id} of profile {configuration_id}, and a "
                "role call could act on any of them: the VCO takes one and "
                "only logs a warning. The save stops before it sends a role "
                "call for the hub, so that no role is set on a service the "
                "profile may not use. Remove the services the VCO holds twice "
                "(in the Orchestrator, the hub's configuration for the "
                "profile), so that one is left, then save again.",
                edges=[
                    (service.edge_id, service.name)
                    for service in found
                    if isinstance(service.edge_id, int)
                ],
            )
        return found[0] if found else None

    def cluster(self, logical_id: uuid.UUID) -> EdgeHubClusterService | None:
        """The hub cluster with logical ID *logical_id*, or None."""
        return next((c for c in self.clusters if c.logical_id == logical_id), None)

    @staticmethod
    def cluster_roles(
        cluster: EdgeHubClusterService, configuration_id: int, segment_id: int
    ) -> EdgeRoles | None:
        """The roles *cluster* has in segment *segment_id* of profile *configuration_id*, or None when its data holds none there.

        A cluster's ``data`` is by configuration ID and then segment ID, and
        a cluster that never was a hub of the profile has no entry for it.
        """
        held = (cluster.data.model_extra or {}).get(str(configuration_id))
        entry = held.get(str(segment_id)) if is_dict(held) else None
        roles = entry.get("roles") if is_dict(entry) else None
        try:
            return EdgeRoles.model_validate(roles) if is_dict(roles) else None
        except ValidationError:
            return None


def _has_configuration(
    service: EdgeHubService,
    configuration_id: int,
    module_id: int,
    segment_object_id: int,
) -> bool:
    """Whether a row of that profile, module and segment points at *service*."""
    held = service.configuration
    return isinstance(held, list) and any(
        c.id_ == configuration_id
        and c.module_id == module_id
        and c.segment_object_id == segment_object_id
        for c in held
    )


async def read_services(
    client: VcoClientProtocol, enterprise_id: int | None, *, qos_usage: bool
) -> HubServices:
    """Read the hub services of the enterprise: two requests, the Edge hubs and then the clusters.

    The Edge hubs come with the ``configuration`` rows that point at them,
    which say the profile, module and segment each is the service for, and
    with ``edgeQOSUsage`` when *qos_usage* (an Edge hub leaves a Branch-to-Hub
    list).
    """
    enterprise = enterprise_id if enterprise_id is not None else MISSING
    with_ = [EnterpriseServicesParamsWith.CONFIGURATION]
    if qos_usage:
        with_.append(EnterpriseServicesParamsWith.EDGE_QOS_USAGE)
    edge_hubs = await client.get_enterprise_services(
        GetEnterpriseServices(
            enterprise_id=enterprise, type_=EnterpriseServiceType.EDGE_HUB, with_=with_
        )
    )
    clusters = await client.get_enterprise_services(
        GetEnterpriseServices(
            enterprise_id=enterprise, type_=EnterpriseServiceType.EDGE_HUB_CLUSTER
        )
    )
    return HubServices(
        tuple(s for s in edge_hubs if isinstance(s, EdgeHubService)),
        tuple(s for s in clusters if isinstance(s, EdgeHubClusterService)),
    )


def in_place(
    step: HubStep,
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
) -> bool:
    """Whether the VCO is as *step* would leave it, so the save does not send it.

    An enable is in place when the hub's service has the role in that segment.
    An Edge disable is in place when the Edge has no hub service there (the
    VCO answers ``null``), and a cluster disable when the cluster's role is
    off or its data holds no role for the profile and segment (the VCO fails
    that call).
    """
    field = _ROLE_FIELDS[step.role]
    if step.hub.kind == "edge":
        service = services.edge_service(
            step.hub.logical_id, configuration_id, module_id, step.segment_object_id
        )
        if step.action == "disable":
            return service is None
        return service is not None and getattr(service.data.roles, field) is True
    cluster = services.cluster(step.hub.logical_id)
    roles = (
        None
        if cluster is None
        else services.cluster_roles(cluster, configuration_id, step.segment_id)
    )
    has = roles is not None and getattr(roles, field) is True
    return not has if step.action == "disable" else has


type _RoleRequest = (
    EnableEdgeHub
    | EnableEdgeToEdgeBridge
    | EnableEdgeBackHaul
    | DisableEdgeHub
    | DisableEdgeToEdgeBridge
    | DisableEdgeBackHaul
    | EnableClusterHub
    | EnableClusterToEdgeBridge
    | EnableClusterBackHaul
    | DisableClusterHub
    | DisableClusterToEdgeBridge
    | DisableClusterBackHaul
)

_CALLS: Final[dict[tuple[Action, Role, str], tuple[type[_RoleRequest], str]]] = {
    ("enable", "hub", "edge"): (EnableEdgeHub, "enable_edge_hub"),
    ("enable", "bridge", "edge"): (
        EnableEdgeToEdgeBridge,
        "enable_edge_to_edge_bridge",
    ),
    ("enable", "backhaul", "edge"): (EnableEdgeBackHaul, "enable_edge_backhaul"),
    ("disable", "hub", "edge"): (DisableEdgeHub, "disable_edge_hub"),
    ("disable", "bridge", "edge"): (
        DisableEdgeToEdgeBridge,
        "disable_edge_to_edge_bridge",
    ),
    ("disable", "backhaul", "edge"): (DisableEdgeBackHaul, "disable_edge_backhaul"),
    ("enable", "hub", "cluster"): (EnableClusterHub, "enable_cluster_hub"),
    ("enable", "bridge", "cluster"): (
        EnableClusterToEdgeBridge,
        "enable_cluster_to_edge_bridge",
    ),
    ("enable", "backhaul", "cluster"): (
        EnableClusterBackHaul,
        "enable_cluster_backhaul",
    ),
    ("disable", "hub", "cluster"): (DisableClusterHub, "disable_cluster_hub"),
    ("disable", "bridge", "cluster"): (
        DisableClusterToEdgeBridge,
        "disable_cluster_to_edge_bridge",
    ),
    ("disable", "backhaul", "cluster"): (
        DisableClusterBackHaul,
        "disable_cluster_backhaul",
    ),
}
"""Each role call: its request and the method of the client that sends it, by action, role and kind of hub."""


def _target_id(
    step: HubStep, services: HubServices, *, configuration_id: int, module_id: int
) -> int:
    """The Edge ID or cluster ID the call of *step* takes: the step's for an enable, the one *services* give for a removal."""
    hub = step.hub
    if step.action == "enable":
        given = hub.edge_id if hub.kind == "edge" else hub.cluster_id
        if given is None:
            raise ValueError(
                f"the step '{step.render()}' has no {'Edge' if hub.kind == 'edge' else 'cluster'} "
                "ID to call the VCO with: plan it with a loaded hub resolver"
            )
        return given
    if hub.kind == "cluster":
        cluster = services.cluster(hub.logical_id)
        if cluster is None:
            raise _no_service(step, "cluster")
        return cluster.id_
    service = services.edge_service(
        hub.logical_id, configuration_id, module_id, step.segment_object_id
    )
    if service is None or not isinstance(service.edge_id, int):
        raise _no_service(step, "Edge hub service")
    return service.edge_id


def _no_service(step: HubStep, what: str) -> HubServiceNotFoundError:
    return HubServiceNotFoundError(
        hub=step.hub.name,
        segment=step.segment,
        path=f'deviceSettings: segments["{step.segment}"].vpn',
        message=(
            f"The VCO has no {what} for {step.hub.name!r} in segment "
            f"{step.segment!r} to call for the step '{step.render()}'. The save "
            "does not send a step whose roles are in place (`in_place`), so the "
            "VCO changed between the read and the call. Save again: it reads "
            "the services again."
        ),
    )


async def send_step(
    client: VcoClientProtocol,
    step: HubStep,
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
    enterprise_id: int | None,
) -> None:
    """Make the one role call of *step*: a removal's Edge or cluster ID comes from *services*.

    An Edge disable the VCO answers with ``null`` counts as done. Any other
    error of the call propagates.
    """
    request_type, method = _CALLS[step.action, step.role, step.hub.kind]
    request = request_type(
        id_=_target_id(
            step, services, configuration_id=configuration_id, module_id=module_id
        ),
        configuration_id=configuration_id,
        module_id=module_id,
        segment_object_id=step.segment_object_id,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
    )
    send = cast(Callable[[_RoleRequest], Awaitable[object]], getattr(client, method))
    _ = await send(request)


def _hub_ref_name(hub: HubRef) -> str:
    return EDGE_HUB_REF if hub.kind == "edge" else CLUSTER_HUB_REF


def _holds_row(
    raw_refs: object, ref: str, object_id: int, segment_object_id: int
) -> bool:
    return any(
        row.get("enterpriseObjectId") == object_id
        and row.get("segmentObjectId") == segment_object_id
        for row in rows_named(raw_refs, ref)
    )


def _row_of_hub(
    raw_refs: object, step: HubStep, configuration_id: int
) -> dict[str, object] | None:
    """The row of the hub of *step* for its segment in *raw_refs*, as read, or None."""
    ref = _hub_ref_name(step.hub)
    for row in rows_named(raw_refs, ref):
        if row.get("configurationId") != configuration_id:
            continue
        if row.get("segmentObjectId") != step.segment_object_id:
            continue
        held = row.get("data")
        if step.hub.kind == "edge":
            logical_id = held.get("logicalId") if is_dict(held) else None
        else:
            logical_id = row.get("logicalId")
        if str(logical_id).lower() == str(step.hub.logical_id):
            return cast(dict[str, object], row)
    return None


def hub_rows(
    as_read_refs: object,
    steps: Sequence[HubStep],
    services: HubServices,
    *,
    configuration_id: int,
    module_id: int,
    now: DeviceSettingsData,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """The hub rows the steps add to and remove from the refs of the device settings module: ``(added, removed)``.

    *services* are read after the steps were sent, *as_read_refs* are the
    module's refs as read, and *now* the module as edited. A hub added to a
    Branch-to-Hub list has a row of five fields (``configurationId``,
    ``moduleId``, ``enterpriseObjectId`` which is its service's ID,
    ``ref`` and ``segmentObjectId``), unless the refs as read already hold one
    of the same ``(ref, enterpriseObjectId, segmentObjectId)``: the VCO's table
    has no unique constraint, so a second would be stored. A hub removed from
    it loses its row as read. A row of a hub that stays in the list is never
    removed. Raises ``HubServiceNotFoundError`` for a hub the VCO has no
    service for after its enable.
    """
    lists = _lists(now)
    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    for step in steps:
        if step.role != "hub":
            continue
        ref = _hub_ref_name(step.hub)
        if step.action == "enable":
            if step.hub.kind == "edge":
                service = services.edge_service(
                    step.hub.logical_id,
                    configuration_id,
                    module_id,
                    step.segment_object_id,
                )
                object_id = None if service is None else service.id_
            else:
                cluster = services.cluster(step.hub.logical_id)
                object_id = None if cluster is None else cluster.id_
            if object_id is None:
                raise _no_service(step, "service")
            if _holds_row(as_read_refs, ref, object_id, step.segment_object_id):
                continue
            row: dict[str, object] = {
                "configurationId": configuration_id,
                "moduleId": module_id,
                "enterpriseObjectId": object_id,
                "ref": ref,
                "segmentObjectId": step.segment_object_id,
            }
            if row not in added:
                added.append(row)
            continue
        stays = any(
            h.logical_id == step.hub.logical_id
            for h in lists.get(step.segment_id, {}).get("hub", [])
        )
        gone = None if stays else _row_of_hub(as_read_refs, step, configuration_id)
        if gone is not None and gone not in removed:
            removed.append(gone)
    return added, removed
