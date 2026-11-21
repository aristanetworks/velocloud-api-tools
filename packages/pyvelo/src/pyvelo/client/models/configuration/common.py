import copy
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal, Protocol, cast

from pydantic import MISSING

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.exceptions import UnboundError
from pyvelo.core import Alias, ConfigContext, VcoModel
from pyvelo.core.base import (
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.core.fields import NameOf, Resolvable
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
    name: Annotated[str, NameOf(SegmentIdentity)]
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


def profile_placeholder(
    profile_segments: Sequence[Mapping[str, object]] | None,
    name: str,
    module_name: str,
) -> dict[str, Any]:
    """The empty segment entry for the profile segment called *name*.

    Raises ``UnboundError`` if *profile_segments* is None (the data is not bound
    to a profile) and ``ValueError`` if the profile has no segment of that name.
    """
    if profile_segments is None:
        raise UnboundError(
            f"segment {name!r} is not in this layer, and this object is not bound to a profile; load it through ConfigPipeline"
        )
    available: list[str] = []
    for entry in profile_segments:
        descriptor = entry.get("segment")
        if not _is_dict(descriptor):
            continue
        if descriptor.get("name") == name:
            return placeholder_segment(entry, module_name)
        available.append(str(descriptor.get("name")))
    raise ValueError(
        f"Segment {name!r} not found. Profile segments: {', '.join(available)}"
    )


def copy_profile_segments(profile_data: object) -> list[dict[str, Any]]:
    """A deep copy of the segment entries of a profile module's raw *profile_data*.

    An empty list if *profile_data* is not an object or has no ``segments`` list;
    entries that are not objects are dropped.
    """
    if not _is_dict(profile_data):
        return []
    segments = profile_data.get("segments")
    if not _is_list(segments):
        return []
    return [
        cast(dict[str, Any], copy.deepcopy(entry))
        for entry in segments
        if _is_dict(entry)
    ]


class _HasSegment(Protocol):
    @property
    def segment(self) -> SegmentMetadata: ...


class _Validate[S](Protocol):
    def __call__(
        self, obj: object, /, *, context: dict[str, ConfigContext] | None = None
    ) -> S: ...


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


def get_or_create_segment[S: _HasSegment](
    segments: list[S],
    name: str,
    profile_segments: Sequence[Mapping[str, object]] | None,
    module_name: str,
    validate: _Validate[S],
    level: ConfigContext | None,
) -> S:
    """The entry of *segments* called *name*, adding an empty one if the layer lacks it.

    An entry under another name but with the profile segment's ``segmentId`` is
    that segment, renamed, and is returned as it is. Otherwise a placeholder
    (see :func:`profile_placeholder`) is validated by *validate* at config level
    *level* and appended.
    """
    for seg in segments:
        if seg.segment.name == name:
            return seg
    placeholder = profile_placeholder(profile_segments, name, module_name)
    segment_id = segment_id_of(placeholder)
    if segment_id is not None:
        for seg in segments:
            if seg.segment.segment_id == segment_id:
                return seg
    created = validate(
        placeholder, context={"config_level": level} if level is not None else None
    )
    segments.append(created)
    return created
