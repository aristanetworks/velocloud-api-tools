"""Plan the write of a configuration module: the raw data as read, plus the caller's changes.

``plan_module`` is the one way a typed model becomes a write payload. The payload
starts from the JSON the module was read from (``raw_data``), so keys the model
does not know, values it coerced and defaults it added never reach the VCO. Only
what the caller changed (the difference between the module as re-parsed from the
raw JSON and the model as it is now) is applied to it.

Segment reconciliation: an edge module may lack a segment entry that its profile
has. The write then carries an empty entry for it (the profile's descriptor and
no rules), so the edge layer has a place to hold the segment. It never writes
only to reconcile: with no change from the caller the plan is ``none``.
"""

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import MISSING

from pyvelo.client.exceptions import LossyFieldError
from pyvelo.client.models.configuration.common import (
    SEGMENT_LISTS,
    placeholder_segment,
    segment_id_of,
)
from pyvelo.client.schemas.configuration import ConfigurationModuleBase
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.base import (
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.core.rebase import RawChange, find_lossy_assignments, raw_changes, rebase

type _Operation = Literal["none", "update", "insert"]


def wire_dump(model: VcoModel, level: ConfigContext | None) -> dict[str, Any]:
    """The JSON a model is written as, at config level *level*."""
    return model.model_dump(
        mode="json",
        by_alias=True,
        context={"config_level": level} if level else None,
    )


def missing_segments(
    edge_data: object, profile_data: object, module_name: str
) -> list[dict[str, Any]]:
    """Empty segment entries for the profile's segments that the edge does not have.

    Segments are matched by ``segmentId``, in profile order. Nothing for a module
    that has no segment lists, for data that is not an object, or for a
    ``segments`` that is not a list (an absent or null one on the edge side is an
    empty list). An entry without a ``segmentId`` is skipped on the profile side
    and ignored on the edge side.
    """
    if module_name not in SEGMENT_LISTS:
        return []
    if not (_is_dict(edge_data) and _is_dict(profile_data)):
        return []
    profile_segments = profile_data.get("segments")
    edge_segments: object = edge_data.get("segments")
    if edge_segments is None:
        edge_segments = []
    if not (_is_list(profile_segments) and _is_list(edge_segments)):
        return []
    seen = [segment_id_of(entry) for entry in edge_segments]
    missing: list[dict[str, Any]] = []
    for entry in profile_segments:
        segment_id = segment_id_of(entry)
        if segment_id is None or segment_id in seen:
            continue
        seen.append(segment_id)  # a profile that repeats an id still gets one entry
        missing.append(
            placeholder_segment(cast(Mapping[str, object], entry), module_name)
        )
    return missing


@dataclass(frozen=True)
class ModulePlan:
    """What a save of one module writes."""

    name: str
    operation: _Operation
    data: dict[str, Any]
    """The payload; the raw data as read when *operation* is ``none``."""
    refs: dict[str, Any] | None
    """The complete refs to send, or None to leave refs out."""
    changes: tuple[RawChange, ...]
    """The caller's changes, relative to the raw data with the reconciled segments."""
    reconciled_segments: tuple[str, ...]
    """The profile's segments that the edge module, as read, does not have."""

    def render(self) -> str:
        lines = [f"{self.name}: {self.operation}"]
        for change in self.changes:
            if change.before is MISSING:
                lines.append(f"  + {change.path}: {_json(change.after)}")
            elif change.after is MISSING:
                lines.append(f"  - {change.path}")
            else:
                lines.append(
                    f"  ~ {change.path}: {_json(change.before)} -> {_json(change.after)}"
                )
        if self.refs is not None:
            lines.append("  refs: replaced")
        if self.reconciled_segments:
            label = "missing" if self.operation == "none" else "reconciled"
            lines.append(f"  {label} segments: {', '.join(self.reconciled_segments)}")
        return "\n".join(lines)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _attribute(obj: object, name: str) -> object:
    """``getattr`` typed ``object``; ``MISSING`` if there is no such attribute."""
    value: object = getattr(obj, name, MISSING)
    return value


def _reparse[M: VcoModel](
    model_type: type[M], raw: object, mode: Literal["python", "json"]
) -> M:
    """Parse *raw* the way the module was first parsed."""
    if mode == "json":
        return model_type.model_validate_json(json.dumps(raw))
    return model_type.model_validate(raw)


def _segment_name(placeholder: Mapping[str, Any]) -> str:
    descriptor = cast(Mapping[str, object], placeholder["segment"])
    name = descriptor.get("name")
    return name if isinstance(name, str) else str(descriptor.get("segmentId"))


def _with_baseline_segments(
    current: dict[str, Any],
    baseline: dict[str, Any],
    extra: list[dict[str, Any]],
) -> dict[str, Any]:
    """*current*, plus the baseline's version of each reconciled segment it lacks.

    The caller's data has no entry for a segment that only reconciliation adds.
    Without one it would read as the caller deleting it (typed data, in the
    rebase) or as a change to ``segments`` (a plain dict, which is the payload).
    An absent or null ``segments`` in *current* is an empty list.
    """
    current_segments: object = current.get("segments")
    if current_segments is None:
        current_segments = []
    baseline_segments = baseline.get("segments")
    if not (_is_list(current_segments) and _is_list(baseline_segments)):
        return current
    have = [segment_id_of(entry) for entry in current_segments]
    added: list[object] = []
    for placeholder in extra:
        segment_id = segment_id_of(placeholder)
        if segment_id in have:
            continue
        entry = next(
            (e for e in baseline_segments if segment_id_of(e) == segment_id), None
        )
        if entry is not None:
            added.append(entry)
    if not added:
        return current
    return {**current, "segments": [*current_segments, *added]}


def _plan_refs(
    module: ConfigurationModuleBase,
    refs: VcoModel | Mapping[str, object] | None,
    level: ConfigContext | None,
) -> dict[str, Any] | None:
    """The whole refs to send, or None when they are not there or did not change."""
    current = refs if refs is not None else _attribute(module, "refs")
    raw = module.raw_refs
    if isinstance(current, VcoModel):
        after: dict[str, Any] = wire_dump(current, level)
        model_type: type[VcoModel] | None = type(current)
    elif isinstance(current, Mapping):
        after = copy.deepcopy(dict(cast(Mapping[str, Any], current)))
        model_type = None
    else:  # MISSING, None: the module has no refs to send
        return None
    if raw is MISSING or raw is None:
        candidate: object = after
    else:
        before: object = raw
        if model_type is not None:
            before = wire_dump(_reparse(model_type, raw, module.raw_mode), level)
        candidate = copy.deepcopy(rebase(raw, before, after, model_type))
    return cast(dict[str, Any], candidate) if candidate != raw else None


def plan_module(
    module: ConfigurationModuleBase,
    data: VcoModel | Mapping[str, object] | None = None,
    *,
    refs: VcoModel | Mapping[str, object] | None = None,
    level: ConfigContext | None = None,
    profile: ConfigurationModuleBase | None = None,
) -> ModulePlan:
    """Plan the write of *module*, with *data* (default ``module.data``) as its data.

    *level* defaults to the config level of *data*. *refs* defaults to
    ``module.refs``. Give the *profile*'s module of the same name to reconcile
    the segments an edge module lacks (never at the profile level).

    Raises ``LossyFieldError`` if the caller assigned a value that cannot reach
    the written data (the default of a key that was absent when read).
    """
    name = str(_attribute(module, "name"))
    current = data if data is not None else _attribute(module, "data")
    if level is None and isinstance(current, VcoModel):
        level = current.config_context
    refs_out = _plan_refs(module, refs, level)

    raw = module.raw_data
    if raw is MISSING:  # a module object that never carried data
        payload = (
            wire_dump(current, level)
            if isinstance(current, VcoModel)
            else copy.deepcopy(dict(cast(Mapping[str, Any], current)))
        )
        model_type = type(current) if isinstance(current, VcoModel) else None
        return ModulePlan(
            name=name,
            operation="update",
            data=payload,
            refs=refs_out,
            changes=tuple(raw_changes({}, payload, model_type)),
            reconciled_segments=(),
        )

    extra: list[dict[str, Any]] = []
    if profile is not None and level is not ConfigContext.PROFILE:
        extra = missing_segments(raw, profile.raw_data, name)
    reconciled = copy.deepcopy(raw)
    if extra and _is_dict(reconciled):
        segments = reconciled.get("segments")
        if _is_list(segments):
            segments.extend(extra)
        else:  # absent or null
            reconciled["segments"] = list(extra)

    if isinstance(current, VcoModel):
        model_type = type(current)
        baseline = _reparse(model_type, reconciled, module.raw_mode)
        before = wire_dump(baseline, level)
        after = wire_dump(current, level)
        payload = cast(
            dict[str, Any],
            copy.deepcopy(
                rebase(
                    reconciled,
                    before,
                    _with_baseline_segments(after, before, extra),
                    model_type,
                )
            ),
        )
        lossy = find_lossy_assignments(baseline, current, before, after)
        if lossy:
            raise LossyFieldError(name, lossy)
    else:
        model_type = None
        topped_up = dict(cast(Mapping[str, Any], current))
        if extra:  # only for an object, so *reconciled* is one
            topped_up = _with_baseline_segments(
                topped_up, cast(dict[str, Any], reconciled), extra
            )
        payload = copy.deepcopy(topped_up)

    changed = payload != reconciled or refs_out is not None
    operation: _Operation = (
        "none" if not changed else "insert" if module.is_new else "update"
    )
    return ModulePlan(
        name=name,
        operation=operation,
        data=payload if changed else copy.deepcopy(cast(dict[str, Any], raw)),
        refs=refs_out,
        changes=tuple(raw_changes(reconciled, payload, model_type)),
        reconciled_segments=tuple(_segment_name(entry) for entry in extra),
    )
