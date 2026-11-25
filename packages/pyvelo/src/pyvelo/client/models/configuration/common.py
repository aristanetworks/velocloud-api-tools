"""What the configuration modules share: segment metadata, group refs, segment lookup.

``SegmentMetadata`` is the ``segment`` descriptor of a segment entry. The functions
find a segment entry of a module by name or ID, add an empty one a layer lacks
(``get_or_create_segment``), and bind a layer's data to the profile module it
inherits from (``bind_profile_layers``).
"""

import copy
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal, Protocol, cast

from pydantic import MISSING

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.exceptions import UnboundError
from pyvelo.core import Alias, ConfigContext, VcoModel
from pyvelo.core.base import (
    is_dict,
    is_list,
)
from pyvelo.core.fields import NameOf, Resolvable
from pyvelo.core.identity import (
    AddressGroupIdentity,
    SegmentIdentity,
    ServiceGroupIdentity,
)
from pyvelo.core.layers import Edge, Layered, Profile
from pyvelo.core.owned import bind_layers, bind_new_item

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
    uuid.UUID | MISSING, Resolvable(AddressGroupIdentity, lambda i: i.logical_id)
]
ServiceGroupRef = Annotated[
    uuid.UUID | MISSING, Resolvable(ServiceGroupIdentity, lambda i: i.logical_id)
]


class SegmentMetadata(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0
    name: Annotated[str, NameOf(SegmentIdentity)]
    type_: Annotated[
        NetworkSegmentType,
        Alias("type"),
        Resolvable(SegmentIdentity, lambda i: i.type_),
    ] = NetworkSegmentType.REGULAR
    segment_logical_id: Annotated[
        uuid.UUID | None, Resolvable(SegmentIdentity, lambda i: i.logical_id)
    ] = None


SEGMENT_LISTS: Final[dict[str, tuple[str, ...]]] = {
    "firewall": ("outbound",),
    "QOS": ("rules",),
    "deviceSettings": ("routes.static",),
}
"""Per module, the lists inside a segment entry that belong to one layer.

Each is a dotted path to a list; the objects on the way to it are created.

A segment entry a layer does not have yet is written with these lists empty,
so it carries no rules of the profile it was copied from. A deviceSettings entry
needs ``routes.static`` to be there: the Orchestrator refuses an entry without
it ("static route is missing"), and accepts it empty.
"""


def segment_id_of(raw_segment: object) -> object | None:
    """``raw_segment["segment"]["segmentId"]``, or None if any step is missing."""
    if not is_dict(raw_segment):
        return None
    descriptor = raw_segment.get("segment")
    if not is_dict(descriptor):
        return None
    return descriptor.get("segmentId")


def placeholder_segment(
    profile_segment: Mapping[str, object], module_name: str
) -> dict[str, Any]:
    """An empty segment entry for *module_name* with the profile's descriptor."""
    entry: dict[str, Any] = {"segment": copy.deepcopy(profile_segment["segment"])}
    for path in SEGMENT_LISTS[module_name]:
        *parents, name = path.split(".")
        container = entry
        for key in parents:
            container = container.setdefault(key, {})
        container[name] = []
    return entry


def profile_placeholder(
    profile_segments: Sequence[Mapping[str, object]] | None,
    name: str,
    module_name: str,
) -> dict[str, Any]:
    """The empty segment entry for the profile segment called *name*.

    An exact match wins; failing that, the first one that differs only in case
    (the resolvers match names the same way).

    Raises ``UnboundError`` if *profile_segments* is None (the data is not bound
    to a profile) and ``ValueError`` if the profile has no segment of that name.
    """
    if profile_segments is None:
        raise UnboundError(
            f"segment {name!r} is not in this layer, and this object is not bound "
            "to a profile; load it through ConfigPipeline"
        )
    entry = _profile_segment_called(profile_segments, name)
    if entry is not None:
        return placeholder_segment(entry, module_name)
    available = [n for e in profile_segments if (n := _segment_name(e)) is not None]
    raise ValueError(
        f"Segment {name!r} not found. Profile segments: {', '.join(available)}"
    )


def profile_segment_name(
    profile_segments: Sequence[Mapping[str, object]] | None, selector: object
) -> str | None:
    """The profile's name for the segment *selector* names, or None if it has none.

    *selector* names a segment by its name exactly, else by its name ignoring
    case (as :func:`profile_placeholder` finds it), else by its ``segmentId``
    in string form. Raises ``UnboundError`` if *profile_segments* is None (the
    data is not bound to a profile).
    """
    entry = _profile_segment_entry(profile_segments, selector)
    return None if entry is None else _segment_name(entry)


def profile_segment_id(
    profile_segments: Sequence[Mapping[str, object]] | None, selector: object
) -> object | None:
    """The ``segmentId`` of the profile segment *selector* names, or None.

    The segment is found as :func:`profile_segment_name` finds it, and the
    same ``UnboundError`` is raised.
    """
    entry = _profile_segment_entry(profile_segments, selector)
    return None if entry is None else segment_id_of(entry)


def _profile_segment_entry(
    profile_segments: Sequence[Mapping[str, object]] | None, selector: object
) -> Mapping[str, object] | None:
    """The profile segment entry *selector* names (``profile_segment_name``), or None."""
    if profile_segments is None:
        raise UnboundError(
            f"segment {selector!r}: this object is not bound to a profile; "
            "load it through ConfigPipeline"
        )
    text = str(selector)
    entry = _profile_segment_called(profile_segments, text)
    if entry is not None:
        return entry
    return next(
        (
            e
            for e in profile_segments
            if (segment_id := segment_id_of(e)) is not None and str(segment_id) == text
        ),
        None,
    )


def _segment_name(entry: Mapping[str, object]) -> str | None:
    """``entry["segment"]["name"]``, or None if any step is missing or it is not a string."""
    descriptor = entry.get("segment")
    name = descriptor.get("name") if is_dict(descriptor) else None
    return name if isinstance(name, str) else None


def _profile_segment_called(
    profile_segments: Sequence[Mapping[str, object]], name: str
) -> Mapping[str, object] | None:
    """The profile segment entry called *name*, or None.

    An exact match wins; failing that, the first one that differs only in case
    (the resolvers match names the same way).
    """
    named = [(e, n) for e in profile_segments if (n := _segment_name(e)) is not None]
    exact = next((e for e, n in named if n == name), None)
    if exact is not None:
        return exact
    return next((e for e, n in named if n.lower() == name.lower()), None)


def copy_profile_segments(profile_data: object) -> list[dict[str, Any]]:
    """A deep copy of the segment entries of a profile module's raw *profile_data*.

    An empty list if *profile_data* is not an object or has no ``segments`` list;
    entries that are not objects are dropped.
    """
    if not is_dict(profile_data):
        return []
    segments = profile_data.get("segments")
    if not is_list(segments):
        return []
    return [
        cast("dict[str, Any]", copy.deepcopy(entry))
        for entry in segments
        if is_dict(entry)
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


def get_or_create_segment[S: _HasSegment](  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    segments: list[S],
    name: str,
    profile_segments: Sequence[Mapping[str, object]] | None,
    module_name: str,
    validate: _Validate[S],
    level: ConfigContext | None,
    *,
    owner: Layered[Any] | None = None,
) -> S:
    """The entry of *segments* called *name*, adding an empty one if the layer lacks it.

    An entry under another name but with the profile segment's ``segmentId`` is
    that segment, renamed, and is returned as it is. Otherwise a placeholder
    (see :func:`profile_placeholder`) is validated by *validate* at config level
    *level* and appended. When *owner*, the module data that holds *segments*,
    is an edge layer, the placeholder is bound to the segment of the profile
    *owner* is bound to (see :func:`pyvelo.core.owned.bind_new_item`), so its
    keys can be localized; an unbound *owner* leaves it unbound.
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
    if owner is not None and owner.layer() is Edge:
        bind_new_item(cast("Layered[Edge]", owner), "segments", cast("Any", created))
    return created


def bind_profile_layers(
    data: Layered[Any],
    profile_data: object,
    profile_class: type[Layered[Profile]],
) -> list[dict[str, Any]]:
    """Link *data* to the profile module it inherits from, and return the profile's segments for ``_profile_segments``.

    *profile_data* is the profile module's raw ``data`` (anything else, as for
    an edge whose profile has no such module, is a profile with no segments).
    The segments returned are copies, which ``get_or_create_segment`` copies a
    missing segment's descriptor from. On an edge layer, *data* and what is in
    it are also bound to the profile layer read from the data
    (*profile_class*), which is where ``localize`` copies from: see
    ``pyvelo.core.owned.bind_layers``. Raises ``ValidationError`` for a
    profile module that does not parse.
    """
    segments = copy_profile_segments(profile_data)
    if data.layer() is not Edge:
        return segments
    raw: dict[str, Any] = {"segments": []}
    if is_dict(profile_data):
        raw = {**cast("dict[str, Any]", profile_data), "segments": segments}
    bind_layers(cast("Layered[Edge]", data), profile_class.model_validate(raw))
    return segments
