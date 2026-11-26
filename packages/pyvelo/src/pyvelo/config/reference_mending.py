"""The patches that mend a reference to a hub that is no backhaul hub of its segment.

A QoS rule's backhaul entry names a hub, and the hub must be a backhaul hub of
the rule's segment. When it is not, there are two ways to mend it: the rule
stops using the hub (a ``remove_reference`` patch, to the module that holds the
rule), or the hub becomes a backhaul hub of the segment (an ``add_member``
patch, to the profile's device settings). ``hub_reference_mending`` builds both
for every hub reference ``ConfigState.plan()`` refuses, and
``DanglingReferenceError.mending`` carries them.
"""

from collections.abc import Collection, Mapping, Sequence
from typing import Literal

from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.core import VcoModel, unwrap
from pyvelo.core.mending import MendingPatch, backhaul_hub_mending
from pyvelo.core.references import Reference, removal_patch


def hub_list_members(device_settings: object) -> dict[int, frozenset[object]]:
    """The logical IDs in each segment's Branch-to-Hub list of *device_settings*.

    By segment ID. ``{}`` for anything that is not device settings data.
    """
    if not isinstance(device_settings, DeviceSettingsData):
        return {}
    return {
        segment.segment.segment_id: frozenset(
            hub.logical_id for hub in segment.vpn.hub_edges.vpn_hubs
        )
        for segment in device_settings.segments
    }


def _hub_shown(reference: Reference) -> tuple[str, bool]:
    """The hub of *reference* as a patch names it, and whether that is its logical ID.

    Its name when its entry has one, else its logical ID.
    """
    name: object = unwrap(getattr(reference.holder, "edge_name", None))
    if isinstance(name, str) and name:
        return name, False
    return str(reference.value), True


def _removal(
    reference: Reference,
    edited: Mapping[str, VcoModel],
    target_type: Literal["edge", "profile"],
) -> MendingPatch | None:
    """The patch that takes the hub of *reference* out of its rule, or ``None``."""
    data = edited.get(reference.module)
    patch = removal_patch(data, reference) if data is not None else None
    if patch is None:
        return None
    hub, _ = _hub_shown(reference)
    segment = reference.segment_name or str(reference.segment_id)
    return MendingPatch(
        kind="remove_reference",
        does=(
            f"Take the hub {hub!r} out of the rule that names it, in segment "
            f"{segment!r}"
        ),
        target_type=target_type,
        module=reference.module,
        patch=patch,
    )


def _member(
    reference: Reference, hub_lists: Mapping[int, Collection[object]]
) -> MendingPatch | None:
    """The patch that makes the hub of *reference* a backhaul hub, or ``None``.

    ``None`` when the reference does not name its segment and its ID.
    """
    if reference.segment_name is None or reference.segment_id is None:
        return None
    hub, by_logical_id = _hub_shown(reference)
    return backhaul_hub_mending(
        reference.segment_name,
        hub,
        by_logical_id=by_logical_id,
        in_hub_list=reference.value in hub_lists.get(reference.segment_id, ()),
    )


def hub_reference_mending(
    dangling: Sequence[Reference],
    edited: Mapping[str, VcoModel],
    hub_lists: Mapping[int, Collection[object]],
    *,
    target_type: Literal["edge", "profile"],
) -> tuple[MendingPatch, ...]:
    """The patches that mend the references to a backhaul hub among *dangling*.

    *edited* is the target's module data as edited, by module name;
    *hub_lists* the logical IDs in each segment's Branch-to-Hub list, by
    segment ID (``hub_list_members`` of the device settings that hold the hub
    lists: the profile's); *target_type* the configuration the references are
    in. For each hub reference, in order, a ``remove_reference`` patch for the
    target and an ``add_member`` patch for the profile; the
    ``remove_reference`` patches come first. The patches of one kind together
    mend every hub reference, so a kind that cannot be built for one of them
    is left out whole. An ``add_member`` patch that is already listed (two
    rules of a segment that name one hub) is not listed again. A reference to
    a segment gets no patch.
    """
    hubs = [r for r in dangling if r.set_name == "backhaul_hub"]
    removals = [_removal(r, edited, target_type) for r in hubs]
    members: list[MendingPatch | None] = []
    for reference in hubs:
        member = _member(reference, hub_lists)
        if member is None or member not in members:
            members.append(member)
    return (*_whole(removals), *_whole(members))


def _whole(patches: Sequence[MendingPatch | None]) -> tuple[MendingPatch, ...]:
    """*patches*, or none of them when one could not be built."""
    built = [p for p in patches if p is not None]
    return tuple(built) if len(built) == len(patches) else ()
