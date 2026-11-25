"""Assigning an enterprise segment to a profile: what each of its three modules gets, and the trial that checks it.

A profile holds a segment in each of its device settings, QoS and firewall
modules. ``plan_assignment`` makes the entries one assignment adds (once, so
the rule IDs in them are those every plan sends), ``apply_assignment`` puts
them into modules, and ``assign_on_trial`` is the check run on copies before
the real modules change. ``ConfigPipeline.assign_segment`` and ``apply`` both
use them.

``apply`` reaches the same assignment through a patch, from any of the three
modules: ``prepare_profile_segments`` reads the ``segments`` part of the patch
before anything is merged (an ``add`` of a segment the module had no entry for
assigns it, a list value that brings one does too), and
``check_profile_segments`` is what the patched module must satisfy after the
merge.
"""

import copy
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from pydantic import MISSING

from pyvelo.client.models.configuration.common import SegmentMetadata, segment_id_of
from pyvelo.client.models.configuration.profile_segments import (
    descriptor_of,
    new_segment_entry,
    pair_defaults,
    repaired_entry,
    with_additions,
)
from pyvelo.client.schemas.configuration import AnyStoredModule
from pyvelo.config.state import ConfigState
from pyvelo.core import ConfigContext, PatchError
from pyvelo.core.base import VcoModel, field_models, is_dict, is_list
from pyvelo.core.identity import (
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.merge import select_items
from pyvelo.core.patch import ItemOp, is_new, parse_item_op, position_of
from pyvelo.core.rebase import Key, Path

PROFILE_SEGMENT_MODULES: Final = ("deviceSettings", "QOS", "firewall")
"""The modules of a profile that hold its segments, by wire name, in the order a save writes them."""


def segment_modules(state: ConfigState) -> dict[str, AnyStoredModule]:
    """The three modules of *state*'s profile that hold segments, by wire name.

    Raises ``ValueError`` for an edge target (an edge has the segments of its
    profile), for a profile that lacks one of the three (inserting a module
    into a profile is not supported), and for a module that is not at the
    profile level (load the profile with ``load_profile``, or call
    ``.for_profile()`` on one you built).
    """
    if state.target_type != "profile":
        raise ValueError(
            "An edge has the segments of its profile, so a segment is assigned "
            "to a profile: load the profile with pipeline.load_profile(...)."
        )
    modules: dict[str, AnyStoredModule] = {}
    for name in PROFILE_SEGMENT_MODULES:
        module = state.find_module(name)
        if module is None:
            raise ValueError(
                f"The profile has no {name} module, and a segment is assigned to "
                "a profile that has all three of device settings, QoS and "
                "firewall. Inserting a module into a profile is not supported: "
                "add it in the VCO."
            )
        if module.config_context is not ConfigContext.PROFILE:
            raise ValueError(
                f"The {name} module is not at the profile level, so a segment "
                "cannot be added to it. Load the profile with "
                "pipeline.load_profile(...), or call .for_profile() on a "
                "profile you built."
            )
        modules[name] = module
    return modules


@dataclass(frozen=True)
class Assignment:
    """What assigning *identity* to a profile adds to its modules now."""

    identity: SegmentIdentity
    entries: Mapping[str, Mapping[str, Any]]
    """For each module that lacks the segment, its wire name and the raw entry to add."""
    defaults: tuple[Mapping[str, Any], ...] = ()
    """The template ``defaults`` an existing QoS entry of the segment lacks, with their IDs and ``allowConditionalBh``."""


def _held_segment_ids(module: AnyStoredModule) -> set[int]:
    segments: object = getattr(getattr(module, "data", MISSING), "segments", None)
    if not isinstance(segments, list):
        return set()
    return {
        item.segment.segment_id
        for item in cast(list[Any], segments)
        if getattr(item, "segment", None) is not None
    }


def _raw_defaults(module: AnyStoredModule, segment_id: int) -> object:
    """The ``defaults`` of segment *segment_id* in *module*'s raw data plus its additions, as raw JSON; ``MISSING`` when it has none."""
    raw = with_additions(module.raw_data, module.additions)
    entries = raw.get("segments") if is_dict(raw) else None
    for entry in entries if is_list(entries) else []:
        if is_dict(entry) and segment_id_of(entry) == segment_id:
            return entry.get("defaults", MISSING)
    return MISSING


def plan_assignment(
    state: ConfigState,
    identity: SegmentIdentity,
    *,
    qos_rules: Sequence[Mapping[str, Any]] | None = None,
) -> Assignment:
    """What assigning *identity* to *state*'s profile adds now.

    A module that lacks the segment gets its template entry
    (``new_segment_entry``), made here, once. A QoS entry the profile already
    has that lacks some of the template's four ``defaults`` gets those, with
    new rule IDs and the ``allowConditionalBh`` the segment's device settings
    give, unless its ``defaults`` hold an item the template does not know or
    repeat a name (``pair_defaults``): that list is left alone, and a plan of
    the module warns.

    *qos_rules* are the JSON rules of a new QoS entry, in place of the
    template's. Raises ``ValueError`` when the QoS module already has the
    segment: they would be dropped. Raises ``ValueError`` as
    :func:`segment_modules` does.
    """
    modules = segment_modules(state)
    segment_id = identity.segment_id
    entries: dict[str, Mapping[str, Any]] = {}
    defaults: tuple[Mapping[str, Any], ...] = ()
    for name, module in modules.items():
        if segment_id not in _held_segment_ids(module):
            entries[name] = new_segment_entry(
                name, identity, qos_rules=qos_rules if name == "QOS" else None
            )
        elif name == "QOS":
            if qos_rules is not None:
                raise ValueError(
                    f"qos_rules are the rules of a new QoS entry, and the QoS "
                    f"module already has {identity.name!r}, so they would be "
                    "dropped. Leave qos_rules out and change the rules with a "
                    "patch."
                )
            defaults = _missing_defaults(state, module, identity)
    return Assignment(identity, entries, defaults)


def _missing_defaults(
    state: ConfigState, qos: AnyStoredModule, identity: SegmentIdentity
) -> tuple[Mapping[str, Any], ...]:
    pairing = pair_defaults(_raw_defaults(qos, identity.segment_id))
    if not pairing.missing or pairing.unknown or pairing.clash:
        return ()
    backhaul = state.conditional_backhaul(qos, ConfigContext.PROFILE)
    return tuple(
        repaired_entry(
            name,
            str(uuid.uuid4()),
            None if backhaul is None else backhaul.get(identity.segment_id),
        )
        for name in pairing.missing
    )


def apply_assignment(
    modules: Mapping[str, AnyStoredModule], assignment: Assignment
) -> None:
    """Add *assignment*'s entries and defaults to *modules*, each a deep copy.

    Raises ``ValueError`` as ``add_segment_entry`` does.
    """
    for name, entry in assignment.entries.items():
        _ = modules[name].add_segment_entry(copy.deepcopy(dict(entry)))
    if assignment.defaults:
        modules["QOS"].add_default_entries(
            assignment.identity.segment_id,
            [copy.deepcopy(dict(entry)) for entry in assignment.defaults],
        )


def assign_on_trial(
    state: ConfigState,
    modules: Mapping[str, AnyStoredModule],
    assignments: Sequence[Assignment],
    edit: Callable[[Mapping[str, AnyStoredModule]], None] | None = None,
) -> None:
    """Try *assignments*, then *edit*, on copies of *modules*, and plan each copy.

    Nothing of *state* changes. Raises whatever a step raises: the caller
    applies the same assignments, and the same edit, to the real modules
    only when this returns.
    """
    trial = {name: copy.deepcopy(module) for name, module in modules.items()}
    for assignment in assignments:
        apply_assignment(trial, assignment)
    if edit is not None:
        edit(trial)
    for module in trial.values():
        _ = state.plan_module(module)


# --- the rules of a patch ----------------------------------------------------

SEGMENTS: Final = Path(("segments",))
"""Where the ``segments`` list of a module is in a patch."""

_RULE_ID_KEYS: Final = ("ruleLogicalId", "rule_logical_id")
"""The names a patch can give a QoS rule's ID under."""


def entry_path(path: Path, selector: object) -> Path:
    """Where the entry under *selector* of a ``segments`` object is in the patch.

    The place ``merge_patch`` reports it at: a position for ``"#n"``, else the
    selector in string form.
    """
    n = position_of(selector)
    return path.child(n if n is not None else Key(str(selector)))


def added_segment_name(op: ItemOp) -> str | None:
    """The segment name the body of an ``add`` gives at ``segment.name``, or None.

    That is the key path of every ``segments`` list, and neither ``segment``
    nor ``name`` has an alias or a camelCase form of its own, so these are
    the only keys ``merge_patch`` reads the name under.
    """
    descriptor = op.body.get("segment")
    name = descriptor.get("name") if is_dict(descriptor) else None
    return None if name is None else str(name)


def _segment_descriptors(layer: object) -> list[dict[str, object]]:
    """The wire form of the ``segment`` object of each entry of *layer*, in order."""
    assert isinstance(layer, VcoModel)  # a profile module's data
    dumped = layer.model_dump(mode="json", by_alias=True)
    return [entry["segment"] for entry in dumped["segments"]]


def _canonical(descriptor: Mapping[str, object]) -> str:
    return json.dumps(descriptor, sort_keys=True)


def _raw_segment_ids(raw: object) -> set[int]:
    """The ``segmentId`` of each entry of ``segments`` in the raw data *raw*."""
    entries = raw.get("segments") if is_dict(raw) else None
    return {
        segment_id
        for entry in (entries if is_list(entries) else [])
        if isinstance(segment_id := segment_id_of(entry), int)
    }


def holds_segment_ops(nested: Mapping[str, object]) -> bool:
    """Whether the ``segments`` of the nested patch *nested* holds an ``add`` or a ``delete``, or is a list value.

    These are the patches that need the three modules of a profile. A patch
    of changes and moves alone goes through one module. Raises ``PatchError``
    for a value that is no item operation, as the merge would.
    """
    segments = nested.get("segments")
    if is_list(segments):
        return True
    if not is_dict(segments):
        return False
    return any(
        parse_item_op(value, entry_path(SEGMENTS, selector)).kind in ("add", "delete")
        for selector, value in segments.items()
        if is_dict(value)
    )


@dataclass(frozen=True)
class ProfileSegmentPatch:
    """A nested patch for one profile module, and the assignments it needs first."""

    patch: dict[str, object]
    """The nested patch, rewritten: each assigning ``add`` is a change under the enterprise's name, and a list value's new items are merged into their template entries."""
    assignments: tuple[Assignment, ...]
    """What to add to the modules before *patch* is merged, one per assigned segment."""


type Resolve = Callable[[str], Awaitable[SegmentIdentity]]
"""Resolves a segment name strictly: its identity, ``ResolutionError`` for one the enterprise lacks, ``LookupError`` where nothing can look it up."""


async def prepare_profile_segments(
    state: ConfigState,
    module: AnyStoredModule,
    nested: Mapping[str, object],
    *,
    resolve: Resolve,
) -> ProfileSegmentPatch:
    """The ``segments`` part of *nested*, a patch for *module* of a profile, read before it is merged.

    *module* is one of the profile's device settings, QoS and firewall
    modules; *resolve* gives a name's segment (``ConfigPipeline`` passes
    ``_resolve_segment``, which loads the resolver if it is registered and not
    loaded). For each item of ``segments``:

    - an ``add`` of a segment *module* had no entry for as read assigns it
      (``plan_assignment``) and becomes a change under the enterprise's name,
      with its ``_position``; the name is the selector's, or under ``"+"``
      the one the body gives at ``segment.name``;
    - an ``add`` of a segment *module* had as read raises ``PatchError``
      (``exists``), and an add under ``"+"`` that gives no name, and a body
      whose ``segment`` is not the enterprise segment's, raise ``segment_rule``;
    - a ``delete``, a change and a move are left as they are (what a
      ``delete`` may remove is for ``check_profile_segments``);
    - in a list value, an item for a segment *module* had no entry for as read
      assigns it, and the item is merged into the template entry the
      assignment makes, so a new segment starts from the template whatever
      form of patch brings it. The other items are left as they are.

    Two selectors for one segment raise ``PatchError`` (``duplicate``); a
    name the enterprise lacks raises ``ResolutionError``; a state with no
    segment resolver raises ``PatchError`` (``invalid_value``) at the
    selector, with the ``LookupError`` as its ``__cause__``; a profile that
    lacks one of the three modules, or has one not at the profile level,
    raises ``PatchError`` (``segment_rule``). Nothing changes: the entries are
    made here, once, and the caller applies them (``assign_on_trial``,
    ``apply_assignment``) with the rewritten patch.
    """
    segments = nested.get("segments")
    if not is_dict(segments) and not is_list(segments):
        return ProfileSegmentPatch(dict(nested), ())
    try:
        _ = segment_modules(state)
    except ValueError as error:
        raise PatchError(
            "a patch that adds, removes or replaces the segments of a profile "
            f"works on its three modules together. {error}",
            SEGMENTS,
            "segment_rule",
        ) from error
    reader = _SegmentReader(state, module, resolve)
    if is_list(segments):
        prepared: object = await reader.list_value(segments)
    else:
        prepared = await reader.items(segments)
    return ProfileSegmentPatch(
        {**nested, "segments": prepared}, tuple(reader.assignments)
    )


class _SegmentReader:
    """What ``prepare_profile_segments`` reads of one module, and what it makes."""

    def __init__(
        self, state: ConfigState, module: AnyStoredModule, resolve: Resolve
    ) -> None:
        self.state: ConfigState = state
        self.module: AnyStoredModule = module
        self.module_name: str = str(module.name)
        self.resolve: Resolve = resolve
        self.data: object = getattr(module, "data", MISSING)
        self.as_read: set[int] = _raw_segment_ids(module.raw_data)
        self.assignments: list[Assignment] = []
        self.assigned: dict[int, Assignment] = {}

    async def items(self, segments: Mapping[object, object]) -> dict[object, object]:
        """*segments*, an object of item operations by selector, with each assigning ``add`` made a change."""
        prepared: dict[object, object] = {}
        named: dict[int, Path] = {}
        for selector, value in segments.items():
            where = entry_path(SEGMENTS, selector)
            key, rewritten = selector, value
            segment_id: int | None = None
            if is_dict(value):
                op = parse_item_op(value, where)
                if op.kind == "add" and position_of(selector) is None:
                    identity, rewritten = await self._assigning_add(
                        selector, value, op, where
                    )
                    key, segment_id = identity.name, identity.segment_id
                else:
                    segment_id = self._held_segment_id(selector)
            if segment_id is not None:
                if segment_id in named:
                    raise _duplicate(named[segment_id], where)
                named[segment_id] = where
            prepared[key] = rewritten
        return prepared

    async def list_value(self, items: Sequence[object]) -> list[object]:
        """*items*, a list value, with each item that brings a new segment merged into its template entry."""
        prepared: list[object] = []
        named: dict[int, Path] = {}
        for index, item in enumerate(items):
            replaced = await self._new_item(item, SEGMENTS.child(index), named)
            prepared.append(item if replaced is None else replaced)
        return prepared

    def _held_segment_id(self, selector: object) -> int | None:
        """The ``segmentId`` of the entry *selector* names in the module's data, or None."""
        if not isinstance(self.data, VcoModel):
            return None
        try:
            positions = select_items(
                self.data, "segments", selector, self.state.resolvers
            )
        except ResolverNotLoadedError:
            raise
        except ResolutionError:
            return None  # merge_patch raises it, with the rest of the patch
        if not positions:
            return None
        segments: object = getattr(self.data, "segments", None)
        if not is_list(segments) or positions[0] >= len(segments):
            return None
        held: object = getattr(
            getattr(segments[positions[0]], "segment", None), "segment_id", None
        )
        return held if isinstance(held, int) else None

    async def _identity(self, name: str, where: Path) -> SegmentIdentity:
        try:
            return await self.resolve(name)
        except ResolutionError:
            raise
        except LookupError as error:
            raise PatchError(
                f"{name!r} is a segment name, and this state has no segment "
                "resolver to look it up: give the state its resolvers with "
                "`await pipeline.load_resolvers(state)` (`load` and "
                "`load_profile` do), then patch again",
                where,
                "invalid_value",
            ) from error

    async def _assigning_add(
        self, selector: object, value: Mapping[object, object], op: ItemOp, where: Path
    ) -> tuple[SegmentIdentity, dict[object, object]]:
        """The segment an ``add`` under *selector* assigns, and the change that replaces it."""
        if is_new(selector):
            name = added_segment_name(op)
            if name is None:
                raise PatchError(
                    'an add under "+" names its segment by its body: '
                    '{"+": {"_op": "add", "segment": {"name": "Guest"}}} '
                    '(`add({"segment": {"name": "Guest"}})`), or by the '
                    'selector, {"Guest": {"_op": "add"}} (`add()`)',
                    where,
                    "segment_rule",
                )
        else:
            name = str(selector)
        identity = await self._identity(name, where)
        if identity.segment_id in self.as_read:
            raise PatchError(
                f"{identity.name} is already in {self.module_name}; change it "
                "with a plain object, such as "
                f'{{"segments": {{"{identity.name}": {{...}}}}}}, not an add',
                where,
                "exists",
            )
        change = self._own_defaults_ids(
            _agreeing(identity, {k: v for k, v in value.items() if k != "_op"}, where)
        )
        self._assign(identity)
        return identity, change

    def _assign(self, identity: SegmentIdentity) -> None:
        assignment = plan_assignment(self.state, identity)
        self.assigned[identity.segment_id] = assignment
        if assignment.entries or assignment.defaults:
            self.assignments.append(assignment)

    async def _new_item(
        self, item: object, where: Path, named: dict[int, Path]
    ) -> dict[str, Any] | None:
        """*item*, an item of a list value, merged into its template entry when it names a segment the module had no entry for as read; else None."""
        descriptor = item.get("segment") if is_dict(item) else None
        name = descriptor.get("name") if is_dict(descriptor) else None
        if not is_dict(item) or not isinstance(name, str):
            return None
        held = self._held_segment_id(name)
        if held is not None and held in self.as_read:
            return None
        identity = await self._identity(name, where)
        if identity.segment_id in self.as_read:
            return None
        if identity.segment_id in named:
            raise _duplicate(named[identity.segment_id], where)
        named[identity.segment_id] = where
        body = self._own_defaults_ids(_agreeing(identity, item, where))
        self._assign(identity)
        template = self._template_entry(identity)
        if template is None:
            return None
        return self._merged_item(template, body, where)

    def _own_defaults_ids(self, body: dict[object, object]) -> dict[object, object]:
        """*body* of an assigning add or new list item, without the rule IDs of its QoS ``defaults``.

        The assignment made the entry, so each ``defaults`` item keeps the
        ``ruleLogicalId`` the assignment gave it, whatever the body says: an
        entry copied from another profile carries that profile's IDs, which
        would repeat in this enterprise. Every other field of a ``defaults``
        item is still the template's, or the merge refuses it.
        """
        defaults = body.get("defaults")
        if self.module_name != "QOS" or not (is_list(defaults) or is_dict(defaults)):
            return body
        if is_list(defaults):
            kept: object = [_without_rule_id(item) for item in defaults]
        else:
            kept = {key: _without_rule_id(item) for key, item in defaults.items()}
        return {**body, "defaults": kept}

    def _template_entry(self, identity: SegmentIdentity) -> Mapping[str, Any] | None:
        """The raw entry of *identity* the module gets from the assignment, or has now from an earlier one."""
        assignment = self.assigned.get(identity.segment_id)
        if assignment is not None and self.module_name in assignment.entries:
            return assignment.entries[self.module_name]
        raw = with_additions(self.module.raw_data, self.module.additions)
        entries = raw.get("segments") if is_dict(raw) else None
        return next(
            (
                cast(Mapping[str, Any], entry)
                for entry in (entries if is_list(entries) else [])
                if segment_id_of(entry) == identity.segment_id
            ),
            None,
        )

    def _merged_item(
        self, template: Mapping[str, Any], body: Mapping[object, object], where: Path
    ) -> dict[str, Any]:
        """*body* merged into the entry *template*, as the module's ``segments`` item class parses it, dumped as the wire has it."""
        assert isinstance(self.data, VcoModel)  # _held_segment_id's read
        (item_type,) = field_models(type(self.data).model_fields["segments"])
        item = item_type.model_validate(
            copy.deepcopy(dict(template)),
            context={"config_level": self.module.config_context},
        )
        try:
            _ = item.merge_patch(
                cast("dict[str, object]", dict(body)), resolvers=self.state.resolvers
            )
        except PatchError as error:
            raise PatchError(
                error.message, Path((*where.parts, *error.path.parts)), error.kind
            ) from error
        return item.model_dump(mode="json", by_alias=True, exclude_unset=True)


def _without_rule_id(item: object) -> object:
    """*item*, a ``defaults`` item of a patch, without its ``ruleLogicalId`` under any name; anything else as it is."""
    if not is_dict(item):
        return item
    return {key: value for key, value in item.items() if key not in _RULE_ID_KEYS}


def _duplicate(first: Path, where: Path) -> PatchError:
    return PatchError(
        f"{first} names the same segment: one selector per segment",
        where,
        "duplicate",
    )


def _same(key: str, given: object, wanted: object) -> bool:
    """Whether a descriptor's *given* value is the enterprise's *wanted* one: a name in any case."""
    if key == "name":
        return str(given).casefold() == str(wanted).casefold()
    return given == wanted


def _agreeing(
    identity: SegmentIdentity, body: Mapping[object, object], where: Path
) -> dict[object, object]:
    """*body* of an add or a list item for *identity*, without its ``segment`` once that agrees with the enterprise's.

    ``segmentId``, ``type`` and ``segmentLogicalId`` where the body gives them,
    and its ``name`` in any case, must be the enterprise segment's. The
    descriptor of the entry is the assignment's, so it is left out of what is
    merged.
    """
    if "segment" not in body:
        return dict(body)
    given = body["segment"]
    at = where.child("segment")
    if not is_dict(given):
        raise PatchError(
            'segment is the descriptor object, such as {"name": "Guest"}',
            at,
            "segment_rule",
        )
    expected = descriptor_of(identity)
    meta = SegmentMetadata.model_validate(expected)
    try:
        _ = meta.merge_patch(cast("dict[str, object]", given))
    except PatchError as error:
        raise PatchError(
            error.message, Path((*at.parts, *error.path.parts)), error.kind
        ) from error
    dumped = meta.model_dump(mode="json", by_alias=True)
    differing = [
        key
        for key, wanted in expected.items()
        if not _same(key, dumped.get(key), wanted)
    ]
    if differing:
        raise PatchError(
            f"the segment {identity.name!r} of the enterprise is "
            f"{json.dumps(expected)}, and the body gives another "
            f"{', '.join(differing)}. A segment is assigned as the enterprise "
            "has it: leave `segment` out of the body, or give it as it is",
            at,
            "segment_rule",
        )
    return {key: value for key, value in body.items() if key != "segment"}


def check_profile_segments(
    before: VcoModel,
    after: VcoModel,
    module: AnyStoredModule,
    ds_as_read: object,
    assignments: Sequence[Assignment],
) -> None:
    """Refuse a patch after which *module* of a profile has other segments than before, or other descriptors.

    *before* is the data of *module* and *after* a copy with the patch merged
    in; *ds_as_read* is the raw data of the profile's device settings as read;
    *assignments* are those the patch makes. Every ``segmentId`` of *after*
    must be one of *before*, with the same descriptor, or an assigned one,
    with the descriptor of its identity (``descriptor_of``). A ``segmentId``
    of *before* that *after* lacks is an orphan removal, accepted, when
    *module* is QoS or firewall, had the segment as read and *ds_as_read*
    lacks it; any other is a removal. Raises ``PatchError`` at ``segments``
    (``segment_rule``), naming what is added, removed or re-described and how
    to do it instead; changes nothing.
    """
    held = _descriptors_by_id(before)
    now = _descriptors_by_id(after)
    assigned = {
        a.identity.segment_id: dict(descriptor_of(a.identity)) for a in assignments
    }
    module_name = str(module.name)
    orphans = (
        _raw_segment_ids(module.raw_data) - _raw_segment_ids(ds_as_read)
        if module_name in ("QOS", "firewall")
        else set[int]()
    )
    redescribed = {
        str(held_by.get("name"))
        for segment_id, now_by in now.items()
        if (held_by := held.get(segment_id, assigned.get(segment_id))) is not None
        and _canonical(held_by) != _canonical(now_by)
    }
    added = {
        str(d.get("name"))
        for i, d in now.items()
        if i not in held and i not in assigned
    }
    gone = {i: str(d.get("name")) for i, d in held.items() if i not in now}
    # An entry that gives up its ID for another is re-described, not one removed and one added.
    redescribed |= added & set(gone.values())
    added -= redescribed
    removed = {name for i, name in gone.items() if i not in orphans} - redescribed
    if not (redescribed or added or removed):
        return
    parts: list[str] = []
    if redescribed:
        parts.append(
            "it re-describes " + ", ".join(sorted(redescribed)) + ": the "
            "descriptor of a segment (its name, `segmentId`, type and logical "
            "ID) is the enterprise's, so leave `segment` out of the patch"
        )
    if added:
        names = sorted(added)
        parts.append(
            "it adds " + ", ".join(names) + " with no assignment: add an "
            "enterprise segment by its name, "
            f'{{"segments": {{"{names[0]}": {{"_op": "add"}}}}}} (`add()`), '
            "which gives it to all three of device settings, QoS and firewall"
        )
    if removed:
        names = sorted(removed)
        parts.append(
            "it removes " + ", ".join(names) + ": a segment of a profile is in "
            "all three of device settings, QoS and firewall, and removing it "
            "from all three is not supported. Only an orphan, a QoS or "
            "firewall segment that the device settings lacked as read, is "
            f'removed, by {{"segments": {{"{names[0]}": {{"_op": "delete"}}}}}} '
            "(`delete()`) or a list value that leaves it out"
        )
    raise PatchError(
        "a patch cannot change which segments a profile has, or what they "
        "are, except by assigning an enterprise segment or removing an orphan: "
        + "; ".join(parts),
        SEGMENTS,
        "segment_rule",
    )


def _descriptors_by_id(layer: VcoModel) -> dict[int, dict[str, object]]:
    """The wire form of the ``segment`` object of each entry of *layer*, by ``segmentId``."""
    return {
        segment_id: descriptor
        for descriptor in _segment_descriptors(layer)
        if isinstance(segment_id := descriptor.get("segmentId"), int)
    }
