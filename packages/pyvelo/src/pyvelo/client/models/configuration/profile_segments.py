"""The factory content of a new segment of a profile, as the Orchestrator UI writes it.

``new_segment_templates.json`` ships beside this module. It holds, for device
settings, QoS and firewall, the entry a profile gets when a segment is
assigned to it, and the parts of each module that belong to no segment. It has
no segment descriptor and no ``ruleLogicalId``: both are added when an entry is
made. A QoS rule takes the ID here. A firewall rule takes none, the VCO issues
it.

Every function returns a copy, so a caller can change what it gets.

A profile module also records what the pipeline adds to it (``AddedSegment``
and ``AddedDefaults``). ``with_additions`` is the raw data of the module plus
those additions: the baseline a plan compares the module with.
"""

import copy
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Final

from pyvelo.core.base import is_dict, is_list
from pyvelo.core.identity import SegmentIdentity

from .common import segment_id_of

TEMPLATE_RESOURCE: Final = "new_segment_templates.json"
DEFAULT_NAMES: Final = (
    "Default-Internet-UDP",
    "Default-Internet-Other",
    "Default-Any-UDP",
    "Default-Any-Other",
)

# A module's name on the wire, and its key in the template file.
_TEMPLATE_KEYS: Final = {
    "deviceSettings": "deviceSettings",
    "QOS": "qos",
    "firewall": "firewall",
}


@cache
def _template() -> dict[str, Any]:
    """The parsed template file, read once. Callers copy what they take from it."""
    text = files(__package__).joinpath(TEMPLATE_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


def segment_template(module_name: str) -> dict[str, Any]:
    """A copy of the template's segment entry for *module_name*, without a descriptor and without rule IDs.

    *module_name* is a module's name on the wire: ``"deviceSettings"``,
    ``"QOS"`` or ``"firewall"``. Any other name raises ``ValueError``.
    """
    key = _TEMPLATE_KEYS.get(module_name)
    if key is None:
        raise ValueError(
            f"A new segment has a template for {', '.join(map(repr, _TEMPLATE_KEYS))} "
            f"only, not for {module_name!r}."
        )
    return copy.deepcopy(_template()[key]["segment"])


def default_rules() -> list[dict[str, Any]]:
    """A copy of the template's four QoS ``defaults``, without IDs, in ``DEFAULT_NAMES`` order."""
    return copy.deepcopy(_template()["qos"]["segment"]["defaults"])


def descriptor_of(identity: SegmentIdentity) -> dict[str, object]:
    """The ``segment`` descriptor of an entry for *identity*, as the wire has it."""
    return {
        "segmentId": identity.segment_id,
        "name": identity.name,
        "type": identity.type_.value,
        "segmentLogicalId": str(identity.logical_id),
    }


def new_segment_entry(
    module_name: str,
    identity: SegmentIdentity,
    *,
    qos_rules: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """The entry of segment *identity* in module *module_name*: its descriptor, then the template's entry.

    For QoS, every rule of ``rules`` and of ``defaults`` gets a new
    ``ruleLogicalId``. With *qos_rules*, ``rules`` is a deep copy of them
    instead of the template's, and each keeps its own ``ruleLogicalId``. A
    firewall rule gets none.
    """
    entry: dict[str, Any] = {
        "segment": descriptor_of(identity),
        **segment_template(module_name),
    }
    if module_name == "QOS":
        if qos_rules is not None:
            entry["rules"] = copy.deepcopy([dict(rule) for rule in qos_rules])
        else:
            _give_ids(entry["rules"])
        _give_ids(entry["defaults"])
    return entry


def _give_ids(rules: list[dict[str, Any]]) -> None:
    for rule in rules:
        rule["ruleLogicalId"] = str(uuid.uuid4())


@dataclass(frozen=True)
class AddedSegment:
    """A segment entry added to a profile module: its ``segmentId``, name and the whole raw entry."""

    segment_id: int
    name: str
    entry: Mapping[str, Any]


@dataclass(frozen=True)
class AddedDefaults:
    """Rules inserted into the ``defaults`` of a QoS segment that was already there.

    *name* is the segment's. *entries* are the raw rules, in the order
    ``insert_defaults`` gives them.
    """

    segment_id: int
    name: str
    entries: tuple[Mapping[str, Any], ...]


type SegmentAddition = AddedSegment | AddedDefaults


def insert_defaults[T](defaults: list[T], entries: Sequence[T]) -> None:
    """Insert *entries* into the ``defaults`` list *defaults*, in place, where a segment has them.

    The one place that decides the order of the entries added, for the raw
    data (``with_additions``) and for the parsed data of a module alike.
    """
    defaults.extend(entries)


def with_additions(raw: object, additions: Sequence[SegmentAddition]) -> object:
    """A deep copy of *raw*, a module's data, with *additions* made to it.

    Each ``AddedSegment`` entry is appended to ``segments`` (an absent or null
    one becomes a list), and each ``AddedDefaults`` is inserted into the
    ``defaults`` of its segment (an absent or null one becomes a list). Raw
    data that is not an object is returned as a copy. Raises ``ValueError``
    for an ``AddedSegment`` whose ``segmentId`` the segments already hold
    (those of *raw*, and the entries added before it) and for an
    ``AddedDefaults`` whose segment they lack.
    """
    result = copy.deepcopy(raw)
    if not additions or not is_dict(result):
        return result
    segments = result.get("segments")
    if not is_list(segments):  # absent or null
        segments = []
        result["segments"] = segments
    for addition in additions:
        held = [e for e in segments if segment_id_of(e) == addition.segment_id]
        if isinstance(addition, AddedSegment):
            if held:
                raise ValueError(
                    f"Segment {addition.name!r} (segmentId {addition.segment_id}) is "
                    "already in the module's data, so it cannot be added again."
                )
            segments.append(copy.deepcopy(dict(addition.entry)))
            continue
        if not held:
            raise ValueError(
                f"The module's data has no segment {addition.name!r} (segmentId "
                f"{addition.segment_id}) to add defaults to."
            )
        (entry, *_) = held
        if not is_dict(entry):
            raise ValueError(f"Segment {addition.name!r} is not an object.")
        defaults = entry.get("defaults")
        if not is_list(defaults):  # absent or null
            defaults = []
            entry["defaults"] = defaults
        insert_defaults(defaults, copy.deepcopy([dict(e) for e in addition.entries]))
    return result
