"""Assigning an enterprise segment to a profile: what each of its three modules gets, and the trial that checks it.

A profile holds a segment in each of its device settings, QoS and firewall
modules. ``plan_assignment`` makes the entries one assignment adds (once, so
the rule IDs in them are those every plan sends), ``apply_assignment`` puts
them into modules, and ``assign_on_trial`` is the check run on copies before
the real modules change. ``ConfigPipeline.assign_segment`` and ``apply`` both
use them.
"""

import copy
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from pydantic import MISSING
from pyvelo.client.models.configuration.common import segment_id_of
from pyvelo.client.models.configuration.profile_segments import (
    new_segment_entry,
    pair_defaults,
    repaired_entry,
    with_additions,
)
from pyvelo.client.schemas.configuration import AnyConfigurationModule
from pyvelo.core import ConfigContext
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.identity import SegmentIdentity

from pyvelo_intent.config.state import ConfigState

PROFILE_SEGMENT_MODULES: Final = ("deviceSettings", "QOS", "firewall")
"""The modules of a profile that hold its segments, by wire name, in the order a save writes them."""


def segment_modules(state: ConfigState) -> dict[str, AnyConfigurationModule]:
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
    modules: dict[str, AnyConfigurationModule] = {}
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


def _held_segment_ids(module: AnyConfigurationModule) -> set[int]:
    segments: object = getattr(getattr(module, "data", MISSING), "segments", None)
    if not isinstance(segments, list):
        return set()
    return {
        item.segment.segment_id
        for item in cast(list[Any], segments)
        if getattr(item, "segment", None) is not None
    }


def _raw_defaults(module: AnyConfigurationModule, segment_id: int) -> object:
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
    state: ConfigState, qos: AnyConfigurationModule, identity: SegmentIdentity
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
    modules: Mapping[str, AnyConfigurationModule], assignment: Assignment
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
    modules: Mapping[str, AnyConfigurationModule],
    assignments: Sequence[Assignment],
    edit: Callable[[Mapping[str, AnyConfigurationModule]], None] | None = None,
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
