"""Aspect navigation helpers used by ConfigPipeline.

These functions read from a ``ConfigState`` plus an ``Aspect`` and walk the
configuration tree. ``ConfigPipeline.read_aspect`` / ``apply_aspect`` use
the navigators here; tests can also use them directly.
"""

from pyvelo.core.base import VcoModel

from pyvelo_intent.config.aspect import Aspect
from pyvelo_intent.config.module_names import resolve_module_name
from pyvelo_intent.config.state import ConfigState


def navigate_to_base(
    state: ConfigState,
    aspect: Aspect,
    segment: str | None,
) -> object:
    """Return the module data (or segment subtree) the aspect lives under."""
    internal_name = resolve_module_name(aspect.module)

    if state.target_type == "edge" and state.edge_config is not None:
        target_config = state.edge_config
    else:
        target_config = state.profile_config

    if not target_config.has_module(internal_name):
        raise ValueError(f"Module {aspect.module!r} not found on config")

    mod = target_config.get_module(internal_name)
    data = mod.data

    if aspect.segment_scoped:
        if segment is None:
            raise ValueError(
                f"Aspect {aspect.name!r} is segment-scoped; 'segment' parameter is required"
            )
        get_segment = getattr(data, "get_segment", None)
        if get_segment is None:
            raise ValueError(
                f"Module data {type(data).__name__} does not support segment navigation"
            )
        data = get_segment(name=segment)

    return data


def get_aspect_data(
    state: ConfigState,
    aspect: Aspect,
    segment: str | None,
) -> object:
    """Walk the aspect's dotted path against the base data."""
    data = navigate_to_base(state, aspect, segment)
    if aspect.path:
        for part in aspect.path.split("."):
            data = getattr(data, part)
    return data


def get_aspect_parent(
    state: ConfigState,
    aspect: Aspect,
    segment: str | None,
) -> tuple[VcoModel, str]:
    """Walk to the parent VcoModel of the aspect target.

    Returns ``(parent_model, final_field_name)`` so a merge_patch can be
    applied at the leaf.
    """
    data = navigate_to_base(state, aspect, segment)

    if not aspect.path:
        raise ValueError(f"Aspect {aspect.name!r} has no path; cannot determine parent")

    parts = aspect.path.split(".")
    for part in parts[:-1]:
        data = getattr(data, part)

    if not isinstance(data, VcoModel):
        raise TypeError(f"Parent of aspect {aspect.name!r} is not a VcoModel")

    return data, parts[-1]
