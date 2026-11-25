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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Final

from pydantic import MISSING

from pyvelo.core.base import VcoModel, is_dict, is_list
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


def module_template_value(module_name: str, key: str) -> object:
    """A deep copy of the template's value at *key* of the module-level part of *module_name*.

    *module_name* is a module's name on the wire (see :func:`segment_template`)
    and *key* a raw key of its ``data``, as the file spells it
    (``"statefulFirewallSettings"``, ``"syslog_forwarding"``). This is what an
    untouched profile holds at a key of the module that belongs to no segment,
    and so what a layer that does not store the key displays. A key the file
    lacks raises ``KeyError``; a module name with no template ``ValueError``.
    """
    template_key = _TEMPLATE_KEYS.get(module_name)
    if template_key is None:
        raise ValueError(
            f"A module has a template for {', '.join(map(repr, _TEMPLATE_KEYS))} "
            f"only, not for {module_name!r}."
        )
    module: dict[str, Any] = _template()[template_key].get("module", {})
    return copy.deepcopy(module[key])


def template_default[M: VcoModel](
    model: type[M], module_name: str, key: str
) -> Callable[[], M]:
    """A factory of the display default of *key* of *module_name*: the template's value, read as *model*.

    It is the ``Default(factory=...)`` of an ``Inheritable`` field whose value
    an untouched profile holds as the template has it
    (:func:`module_template_value`).
    """

    def make() -> M:
        return model.model_validate(module_template_value(module_name, key))

    return make


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


@dataclass(frozen=True)
class DefaultsPairing:
    """How the ``defaults`` of a QoS segment pair with the template's four entries."""

    missing: tuple[str, ...]
    """The template names no item pairs with, in template order."""
    unknown: tuple[int, ...]
    """The positions of the items that no template entry pairs with."""
    clash: tuple[str, ...]
    """The missing names that the list already has under another ``match``."""


def _name_of(item: object) -> object:
    return item.get("name", MISSING) if is_dict(item) else MISSING


def _match_of(item: object) -> object:
    return item.get("match", MISSING) if is_dict(item) else MISSING


def _pairs(items: Sequence[object]) -> dict[str, int]:
    """The position of the item paired with each template entry, by its name.

    Two passes over the template, in order. The first takes the first
    unpaired item with the entry's name and its ``match``; the second takes
    the first unpaired item with its ``match`` alone. Two pairs of template
    entries share a ``match``, so pairing one to one is what tells a list
    that lacks one of a pair from a complete one. Matches compare as raw
    objects, key for key.
    """
    template = default_rules()
    paired: dict[str, int] = {}
    taken: set[int] = set()
    for by_name in (True, False):
        for entry in template:
            name: str = entry["name"]
            if name in paired:
                continue
            for position, item in enumerate(items):
                if position in taken or _match_of(item) != entry["match"]:
                    continue
                if by_name and _name_of(item) != name:
                    continue
                paired[name] = position
                taken.add(position)
                break
    return paired


def pair_defaults(defaults: object) -> DefaultsPairing:
    """Pair the raw ``defaults`` list *defaults* with the template's entries.

    *defaults* is a list, or ``MISSING``, ``None`` or ``[]``, which miss all
    four (see ``_pairs`` for the pairing). Anything else is not a list the
    repair has a say in: nothing is missing.
    """
    if defaults is MISSING or defaults is None:
        defaults = []
    if not is_list(defaults):
        return DefaultsPairing((), (), ())
    paired = _pairs(defaults)
    missing = tuple(name for name in DEFAULT_NAMES if name not in paired)
    unknown = tuple(
        position for position in range(len(defaults)) if position not in paired.values()
    )
    held = {_name_of(item) for item in defaults}
    return DefaultsPairing(
        missing, unknown, tuple(name for name in missing if name in held)
    )


def _arranged(
    items: Sequence[object], entries: Sequence[Mapping[str, Any]]
) -> list[tuple[bool, int]]:
    """The order of *items* and *entries* once placed: ``(True, i)`` is item *i*, ``(False, j)`` is entry *j*.

    An entry of the template goes after the item paired with the nearest
    earlier template entry, or first when none is paired; the entries after
    one item keep the template's order. The items keep theirs. An entry the
    template has no name for goes last.
    """
    paired = _pairs(items)
    after: dict[int | None, list[int]] = {}
    last: list[int] = []
    for j, entry in sorted(
        enumerate(entries), key=lambda e: _template_index(e[1].get("name"))
    ):
        index = _template_index(entry.get("name"))
        if index >= len(DEFAULT_NAMES):
            last.append(j)
            continue
        anchor = next(
            (paired[n] for n in reversed(DEFAULT_NAMES[:index]) if n in paired),
            None,
        )
        after.setdefault(anchor, []).append(j)
    order: list[tuple[bool, int]] = [(False, j) for j in after.get(None, [])]
    for i in range(len(items)):
        order.append((True, i))
        order.extend((False, j) for j in after.get(i, []))
    order.extend((False, j) for j in last)
    return order


def _template_index(name: object) -> int:
    return DEFAULT_NAMES.index(name) if name in DEFAULT_NAMES else len(DEFAULT_NAMES)


def insert_defaults(
    defaults: list[object] | None, entries: Sequence[Mapping[str, Any]]
) -> list[object]:
    """The list *defaults* with the template entries *entries* placed in template order among its items.

    Each entry (found by its name) goes after the item paired with the
    nearest earlier template entry, else first. The items present keep their
    order and are the very objects given; *defaults* itself is not changed.
    This is the one place that decides the order of the entries added, for
    the raw data (``with_additions``) and for the parsed data of a module
    (``ConfigurationModuleBase.add_default_entries``) alike.
    """
    items: list[object] = [] if defaults is None else defaults
    return [
        items[k] if is_item else entries[k] for is_item, k in _arranged(items, entries)
    ]


def repaired_entry(
    name: str, rule_id: str, *, conditional_backhaul: bool | None
) -> dict[str, Any]:
    """The template ``defaults`` entry *name*, with ``ruleLogicalId`` *rule_id*.

    Unless *conditional_backhaul* is None, ``action.allowConditionalBh`` is
    set to it (the segment's ``vpn.conditionalBackhaul``): the template's
    own action has no such key, as the UI writes them.
    """
    entry = next(rule for rule in default_rules() if rule["name"] == name)
    entry["ruleLogicalId"] = rule_id
    if conditional_backhaul is not None:
        entry["action"]["allowConditionalBh"] = conditional_backhaul
    return entry


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
        entry["defaults"] = insert_defaults(
            defaults if is_list(defaults) else None,  # absent or null
            copy.deepcopy([dict(e) for e in addition.entries]),
        )
    return result
