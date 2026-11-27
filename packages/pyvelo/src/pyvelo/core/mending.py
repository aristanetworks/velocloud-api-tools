"""The patches that mend an error, as data, and the one that makes a hub a backhaul hub.

A ``MendingPatch`` is a patch a caller can send to put a state right, with the
arguments of the ``apply`` call that sends it. Every field is a JSON value, so
``dataclasses.asdict`` gives a JSON object a text caller can follow.

This module imports the standard library only, so that the identity errors can
build the patch their message gives.
"""

from dataclasses import dataclass
from typing import Literal

type MendingKind = Literal["remove_reference", "add_member"]


@dataclass(frozen=True)
class MendingPatch:
    """One patch that mends a state, with the ``apply`` arguments that send it.

    ``kind`` is the way of mending: ``"remove_reference"`` makes a value stop
    naming something the configuration lacks, ``"add_member"`` makes the
    configuration have what the value names. ``does`` is one line that says
    what the patch does. ``target_type`` is the configuration the patch is for
    (``"edge"`` or ``"profile"``, as ``ConfigState.target_type`` names it).
    ``module``, ``patch``, ``segment`` and ``path`` are the keyword arguments of
    the ``apply`` call that sends the patch; a patch given in full leaves
    ``segment`` and ``path`` as ``None``.
    """

    kind: MendingKind
    does: str
    target_type: Literal["edge", "profile"]
    module: str
    patch: dict[str, object]
    segment: str | None = None
    path: str | None = None


def backhaul_hub_mending(
    segment: str, hub: str, *, by_logical_id: bool = False, in_hub_list: bool = False
) -> MendingPatch:
    """The patch that makes *hub* a backhaul hub of the segment *segment*.

    A backhaul hub is a hub of the segment too, so the patch adds the hub to the
    segment's Branch-to-Hub list as well as to its backhaul list, and turns on
    what a segment with hubs needs. The patch is for the profile's device
    settings and gives the hub by its name, or by its logical ID when
    *by_logical_id* is true. With *in_hub_list* (the hub is in the
    Branch-to-Hub list already, where an add would be refused as a duplicate)
    the patch leaves that list alone.
    """
    key = "logicalId" if by_logical_id else "name"
    vpn: dict[str, object] = {"enabled": True}
    if not in_hub_list:
        vpn["edgeToEdgeHub"] = {
            "enabled": True,
            "vpnHubs": {"+": {"_op": "add", key: hub}},
        }
    vpn["backHaulEdges"] = {"+": {"_op": "add", key: hub}}
    return MendingPatch(
        kind="add_member",
        does=f"Make {hub!r} a backhaul hub of segment {segment!r}",
        target_type="profile",
        module="deviceSettings",
        patch={"segments": {segment: {"vpn": vpn}}},
    )
