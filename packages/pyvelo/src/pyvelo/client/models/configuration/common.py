import copy
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import MISSING

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.core import Alias, VcoModel
from pyvelo.core.base import _is_dict  # pyright: ignore[reportPrivateUsage]
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import (
    AddressGroupIdentity,
    SegmentIdentity,
    ServiceGroupIdentity,
)

# Firewall rule VLAN scope. "local" restricts the rule to traffic whose
# source/destination VLAN is local to this edge; "end-to-end" matches
# traffic on a VLAN regardless of whether it is local or remote.
VlanType = Literal["local", "end-to-end"]

AddressType = Literal["IPv4", "IPv6", "IPv4v6"]
OverlayAddressPreferenceType = Literal["IPv4", "IPv6"]
RuleMatchType = Literal["prefix", "exact", "network", "netmask", "wildcard"]

# Typed references for address group and service group UUIDs.
# Used by both firewall and QoS rule match models.
AddressGroupRef = Annotated[
    uuid.UUID | MISSING, Resolvable(AddressGroupIdentity, "logical_id")
]
ServiceGroupRef = Annotated[
    uuid.UUID | MISSING, Resolvable(ServiceGroupIdentity, "logical_id")
]


class SegmentMetadata(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    name: str
    type_: Annotated[
        NetworkSegmentType, Alias("type"), Resolvable(SegmentIdentity, "type_")
    ] = NetworkSegmentType.REGULAR
    segment_logical_id: Annotated[
        uuid.UUID | None, Resolvable(SegmentIdentity, "logical_id")
    ] = None


SEGMENT_LISTS: Final[dict[str, tuple[str, ...]]] = {
    "firewall": ("outbound",),
    "QOS": ("rules",),
    "deviceSettings": (),
}
"""Per module, the lists inside a segment entry that belong to one layer.

A segment entry a layer does not have yet is written with these lists empty,
so it carries no rules of the profile it was copied from.
"""


def segment_id_of(raw_segment: object) -> object | None:
    """``raw_segment["segment"]["segmentId"]``, or None if any step is missing."""
    if not _is_dict(raw_segment):
        return None
    descriptor = raw_segment.get("segment")
    if not _is_dict(descriptor):
        return None
    return descriptor.get("segmentId")


def placeholder_segment(
    profile_segment: Mapping[str, object], module_name: str
) -> dict[str, Any]:
    """An empty segment entry for *module_name* with the profile's descriptor."""
    return {
        "segment": copy.deepcopy(profile_segment["segment"]),
        **{key: [] for key in SEGMENT_LISTS[module_name]},
    }


class _HasSegment(Protocol):
    @property
    def segment(self) -> SegmentMetadata: ...


def find_segment[S: _HasSegment](
    segments: Sequence[S],
    *,
    name: str | None = None,
    segment_id: int | None = None,
) -> S:
    if name is None and segment_id is None:
        raise ValueError("Provide name or segment_id")

    for seg in segments:
        if name is not None and seg.segment.name == name:
            return seg
        if segment_id is not None and seg.segment.segment_id == segment_id:
            return seg

    available = [f"{s.segment.name} (id={s.segment.segment_id})" for s in segments]
    raise ValueError(f"Segment not found. Available: {', '.join(available)}")
