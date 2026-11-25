"""The role steps of a save: a hub added to or removed from a segment's ``vpn`` hub lists.

A hub in the hub, bridge or backhaul list of a profile segment works only when
its service has the role, which the VCO's role calls set (the UI's enable and
disable calls). A write of the device settings module cannot do that, so a save
that changes the lists also sends one step per hub and list, before the module
write, in the UI's order. ``derive_steps`` plans them from the module as read
and as edited, and ``role_disagreements`` warns of a hub whose roles as read
do not match the lists it is in. ``check_hub_lists`` refuses, before any call,
a list edit that breaks the UI's rules for the lists. The calls the save makes
for the steps are in ``hub_calls``.
"""

import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pyvelo.client.exceptions import HubListError
from pyvelo.client.hub_services import (
    CLUSTER_HUB_REF,
    EDGE_HUB_REF,
    can_hold_hub_role,
    hub_list_fields,
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
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import unwrap
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.fields import get_hub_role
from pyvelo.core.identity import (
    HubIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.walk import walk_fields

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


def hub_lists_of(data: DeviceSettingsData) -> dict[int, dict[Role, list[VpnHub]]]:
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
        stored: object = getattr(site.model, site.name)
        held = unwrap(stored)
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
    lists_read, lists_now = hub_lists_of(as_read), hub_lists_of(now)
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
        self._hubs: HubResolver | None = hubs
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
    lists_of_segments = hub_lists_of(as_read)
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
    field.role: field.path for field in hub_list_fields()
}
"""The wire path of each role's list, from a segment: the fields marked ``HubRole``."""
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


def list_path(role: Role) -> str:
    """The dotted wire path of the list of *role*, from a segment: ``vpn.edgeToEdgeHub.vpnHubs`` for the Branch-to-Hub list."""
    return ".".join(_LIST_PATHS[role])


def _list_text(role: Role) -> str:
    """How a message names the list of *role*: its label and its wire path."""
    return f"the {_LIST_LABELS[role]} (`{list_path(role)}`)"


def _removal(segment: str, role: Role, hubs: Sequence[VpnHub]) -> str:
    """The patch that removes *hubs* from the list of *role* of *segment*, with the helper beside it."""
    change = {h.name: {"_op": "delete"} for h in hubs}
    return (
        f"{_patch(segment, _LIST_PATHS[role], change)} (`delete()` in Python) {_APPLY}"
    )


def _repeats_removal(segment: str, role: Role, hubs: Sequence[VpnHub]) -> str:
    """The patch that removes the repeats from the list *hubs* of *role* of *segment*: every entry after the first of a logical ID, by position.

    A hub that is listed twice has no selector of its own (its name names both
    entries), so each repeat is named by its place in the list as it is now
    (``"#n"``, ``at(n)`` in Python).
    """
    seen: set[_Key] = set()
    change: dict[str, object] = {}
    for position, hub in enumerate(hubs):
        if _key(hub) in seen:
            change[f"#{position}"] = {"_op": "delete"}
        seen.add(_key(hub))
    return (
        f"{_patch(segment, _LIST_PATHS[role], change)} (`delete()` under `at(n)` in "
        f"Python) {_APPLY}"
    )


def _addition(segment: str, role: Role, hub_name: str) -> str:
    """The patch that adds the hub named *hub_name* to the list of *role* of *segment*."""
    change = {"+": {"_op": "add", "name": hub_name}}
    return f"{_patch(segment, _LIST_PATHS[role], change)} {_APPLY}"


def hub_in_use_message(
    segment: str, hub: str, edges: Sequence[tuple[int, str]], *, refuse: bool
) -> str:
    """What a removal of the Edge hub *hub* from the Branch-to-Hub list of *segment* does to the *edges* whose QoS rules use it, and what to do.

    *refuse* is the message of the error (the save sends no step), else that
    of the warning (the save goes on).
    """
    who = ", ".join(f"{name!r} (Edge ID {edge_id})" for edge_id, name in edges)
    edge_word = "the Edges" if len(edges) > 1 else "the Edge"
    keep = (
        "To keep the hub, put it back in the Branch-to-Hub list, with "
        f"{_addition(segment, 'hub', hub)}. A hub that leaves that list leaves "
        f"the bridge and backhaul lists too (`{list_path('bridge')}`, "
        f"`{list_path('backhaul')}`), so add it to those again, if it was in "
        "them"
    )
    return (
        f"deviceSettings: segment {segment!r}: the hub {hub!r} is used by the "
        f"backhaul rules of {edge_word} {who}. Taking it out of the "
        "Branch-to-Hub list sends the call that disables its hub role, which "
        "deletes the hub's service for this profile and segment and with it "
        "the backhaul refs of those Edges' rules: the rules keep the hub in "
        "their data, and name a hub the VCO has no service for. "
        + (
            "The save stops before it sends any step, so that no service is "
            f"deleted under those rules. Take {hub!r} out of those rules first "
            f"(in the Orchestrator, the Edges' QoS), or remove the hub anyway "
            'with `on_hub_in_use="warn"` (the default). '
            if refuse
            else "The save goes on. To stop instead when a hub is in use, "
            'save with `on_hub_in_use="refuse"`. '
        )
        + f"{keep}."
    )


def _flag(segment: str, path: tuple[str, ...], *, value: bool) -> str:
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
        tuple(list_path(r) for r in roles),
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
                        "List each hub once: its name names both entries, so "
                        "remove the repeats by their position, "
                        f"{_repeats_removal(name, role, lists.get(role, []))}.",
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
                f"{_flag(name, flag_path, value=True)} or remove the hubs with "
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
                f" or turn it off with {_flag(name, flag_path, value=False)}.",
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
                f"{_flag(name, ('vpn', 'edgeToEdgeDetail', 'useCloudGateway'), value=True)}.",
                ["bridge"],
            )
        )

    # Rule 4: a backhaul or bridge hub is a hub of the segment.
    hub_keys = {_key(h) for h in hub_list}
    problems.extend(
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
        for role in ("bridge", "backhaul")
        for hub in lists.get(role, [])
        if _key(hub) not in hub_keys
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
                f"{_flag(name, ('vpn', 'enabled'), value=True)} or remove the hubs, for "
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
    lists_read, lists_now = hub_lists_of(as_read), hub_lists_of(now)
    read_by_id = {_segment_id(s): s for s in as_read.segments}
    changed = [
        segment
        for segment in now.segments
        if (was := read_by_id.get(_segment_id(segment))) is None
        # Not ``==``: the models differ in a private ``_config_context``
        # between the module as read and as edited, whatever their values.
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
