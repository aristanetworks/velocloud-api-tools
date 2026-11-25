"""The effective view: each value of an edge with where it comes from.

``effective_view`` computes ``Data[Effective]`` from an edge layer and the
profile layer it inherits from: every field is a ``Resolved`` (the value, its
``Origin`` and its ``source``), a list of them, a nested view, a value carried
through, or ``MISSING`` where there is nothing to give. ``effective_dump``
writes the view as wire JSON and says which ``Resolved`` each wire path holds.

Two renderings of a path are used and must not be mixed up:

- ``Resolved.source`` is where the value sits *in the layer it came from*:
  dotted wire keys, a list position as that layer stores it
  (``segments[1].outbound[0]``). The same segment can be at another position
  in the profile and in the edge.
- The paths ``effective_dump`` gives name the value in *the view*: dotted wire
  keys, a keyed ``Layered`` child by its key (``segments[<segmentId>]``, the
  alt key when the list has one) and any other list item by its position in
  the view (``[<index>]``). That is the form the server's composed modules are
  compared in.
"""

import copy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from pydantic import MISSING, TypeAdapter
from pydantic_core import PydanticUndefined

from .base import JsonObject, wire_name
from .fields import KeySpec, get_key_spec
from .layers import (
    Authority,
    Edge,
    Effective,
    Kind,
    Layered,
    Profile,
    display_default,
    field_kind,
    profile_stores,
)
from .owned import ByFlag, Owned, item_identity
from .rebase import item_key_text

__all__ = [
    "Authority",
    "Origin",
    "Resolved",
    "effective_dump",
    "effective_view",
]


class Origin(StrEnum):
    """Where an effective value comes from."""

    EDGE = "edge"
    PROFILE = "profile"
    FACTORY = "factory"


@dataclass(frozen=True)
class Resolved[T]:
    """An effective value with its *origin* and its *source*, the wire path in the layer it came from."""

    origin: Origin
    value: T
    source: str

    def __bool__(self) -> bool:
        raise TypeError(
            f"a Resolved ({self.source}) is not a condition: it holds the value "
            + "with its origin and source, and a test of it would be true "
            + "whatever the value is. Test .value (the value the edge runs "
            + "with), or read .origin to see where it comes from"
        )


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


def effective_view[M: Layered[Any]](
    edge: Layered[Edge] | None, profile: Layered[Profile], model: type[M]
) -> M:
    """The effective view of *edge* over *profile*: an instance of *model*, the ``[Effective]`` class of the same ``Layered`` class.

    *edge* is ``None`` for an edge with no row for the module: every origin is
    then ``PROFILE`` or ``FACTORY``. The view is read-only, holds copies of the
    values of the two layers, and is built without validation (no ``Owned`` is
    made). Per field kind:

    - structural: carried through from the profile layer (from the edge layer
      for a child only the edge has); a child that is itself ``Layered`` is
      resolved the same way, and the children of a keyed list are matched by
      key, the alt key first (``segment.segment_id``): the profile's in the
      profile's order, then those only the edge has;
    - ``Inheritable``: the edge's value when it is local (``EDGE``); with
      ``presence_wins``, an object the edge stores even when its flag says
      inherit (``EDGE``); else the profile's value when the profile stores it
      (``PROFILE``); else the display default (``FACTORY``). An ``Authority``
      rule on the field then may replace the result with the profile's value;
    - ``EdgeOnly`` and ``ProfileOnly``: that layer's value (``FACTORY`` with
      the field's default when that layer is not there);
    - ``Additive``: the edge's entries, then the profile's, each a ``Resolved``;
    - ``Unconverted``: ``MISSING``.

    A ``FACTORY`` value's ``source`` is the field's own path, at the position
    the profile has the model (the edge's, for a model only the edge has).
    """
    if model.layer() is not Effective:
        raise TypeError(
            f"effective_view builds the Effective layer, and {model.__name__} is "
            + f"the {model.layer().__name__} layer: give the [Effective] class of "
            + "the same model, Data[Effective]"
        )
    for layer, name in ((edge, "edge"), (profile, "profile")):
        if layer is not None and _base(type(layer)) is not _base(model):
            raise TypeError(
                f"effective_view: the {name} layer is a {type(layer).__name__} and "
                + f"the view a {model.__name__}, which are not one model; give "
                + "the layers and the view of the same model"
            )
    return _build(model, edge, profile, "", "")


def _base(cls: type[Layered[Any]]) -> type:
    """The class *cls* is a layer of: ``Data`` for ``Data[Edge]``."""
    return cls.__pydantic_generic_metadata__["origin"] or cls


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _copy[T](value: T) -> T:
    return copy.deepcopy(value)


def _build[M: Layered[Any]](
    cls: type[M],
    edge: Layered[Edge] | None,
    profile: Layered[Profile] | None,
    edge_at: str,
    profile_at: str,
) -> M:
    """The view of one model: *edge_at* and *profile_at* are where each layer holds it (a source prefix)."""
    own_at = profile_at if profile is not None else edge_at
    values: dict[str, Any] = {}
    for name, info in cls.model_fields.items():
        key = wire_name(cls, name)
        match field_kind(info).kind:
            case Kind.UNCONVERTED:
                values[name] = MISSING
            case Kind.STRUCTURAL:
                values[name] = _structural(
                    cls, name, edge, profile, key, edge_at, profile_at
                )
            case Kind.INHERITABLE:
                values[name] = _inheritable(
                    cls, name, edge, profile, key, edge_at, profile_at
                )
            case Kind.EDGE_ONLY:
                values[name] = _layer_only(
                    cls, name, Origin.EDGE, edge, key, edge_at, own_at
                )
            case Kind.PROFILE_ONLY:
                values[name] = _layer_only(
                    cls, name, Origin.PROFILE, profile, key, profile_at, own_at
                )
            case Kind.ADDITIVE:
                values[name] = _additive(name, edge, profile, key, edge_at, profile_at)
    return cls.model_construct(**values)


def _inheritable(
    cls: type[Layered[Any]],
    name: str,
    edge: Layered[Edge] | None,
    profile: Layered[Profile] | None,
    key: str,
    edge_at: str,
    profile_at: str,
) -> Resolved[object]:
    info = cls.model_fields[name]
    found = field_kind(info)
    edge_source = _join(edge_at, key)
    profile_source = _join(profile_at, key)
    resolved: Resolved[object] | None = None

    owned: object = getattr(edge, name, None) if edge is not None else None
    if isinstance(owned, Owned):
        stored: object = owned.stored  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        encoding = found.encoding
        present = stored is not MISSING and stored is not None
        if (
            owned.is_local
            or present
            and isinstance(encoding, ByFlag)
            and encoding.presence_wins
        ):
            resolved = Resolved(Origin.EDGE, _copy(stored), edge_source)

    profile_value: object = MISSING
    if profile is not None:
        profile_value = getattr(profile, name)
        if resolved is None and profile_stores(profile, name):
            resolved = Resolved(Origin.PROFILE, _copy(profile_value), profile_source)
        authority = next((m for m in found.metadata if isinstance(m, Authority)), None)
        if authority is not None and authority.rule(profile_value):
            resolved = Resolved(Origin.PROFILE, _copy(profile_value), profile_source)

    if resolved is None:
        own_at = profile_at if profile is not None else edge_at
        resolved = Resolved(Origin.FACTORY, display_default(info), _join(own_at, key))
    return resolved


def _layer_only(
    cls: type[Layered[Any]],
    name: str,
    origin: Origin,
    layer: Layered[Any] | None,
    key: str,
    layer_at: str,
    own_at: str,
) -> object:
    """An ``EdgeOnly`` or ``ProfileOnly`` field: *layer*'s value, or the field's default when the layer is not there."""
    value: object = MISSING if layer is None else getattr(layer, name)
    if value is not MISSING:
        return Resolved(origin, _copy(value), _join(layer_at, key))
    default: object = cls.model_fields[name].get_default(call_default_factory=True)
    if default is PydanticUndefined:
        return MISSING
    return Resolved(Origin.FACTORY, default, _join(own_at, key))


def _additive(
    name: str,
    edge: Layered[Edge] | None,
    profile: Layered[Profile] | None,
    key: str,
    edge_at: str,
    profile_at: str,
) -> list[Resolved[object]]:
    entries: list[Resolved[object]] = []
    for origin, layer, at in (
        (Origin.EDGE, edge, edge_at),
        (Origin.PROFILE, profile, profile_at),
    ):
        items: object = getattr(layer, name, None) if layer is not None else None
        if not isinstance(items, list):
            continue
        base = _join(at, key)
        entries.extend(
            Resolved(origin, _copy(item), f"{base}[{position}]")
            for position, item in enumerate(cast("list[object]", items))
        )
    return entries


def _structural(
    cls: type[Layered[Any]],
    name: str,
    edge: Layered[Edge] | None,
    profile: Layered[Profile] | None,
    key: str,
    edge_at: str,
    profile_at: str,
) -> object:
    edge_value: object = getattr(edge, name, MISSING) if edge is not None else MISSING
    profile_value: object = (
        getattr(profile, name, MISSING) if profile is not None else MISSING
    )
    if _has_layered(edge_value) or _has_layered(profile_value):
        if isinstance(edge_value, list) or isinstance(profile_value, list):
            return _children(
                cls, name, edge_value, profile_value, key, edge_at, profile_at
            )
        return _child(edge_value, profile_value, key, edge_at, profile_at)
    return _copy(profile_value if profile is not None else edge_value)


def _has_layered(value: object) -> bool:
    if isinstance(value, list):
        return any(isinstance(item, Layered) for item in cast("list[object]", value))
    return isinstance(value, Layered)


def _view_class(model: Layered[Any]) -> type[Layered[Any]]:
    """The ``[Effective]`` class of the ``Layered`` class *model* is a layer of."""
    base: Any = _base(type(model))
    return base[Effective]


def _child(
    edge_value: object,
    profile_value: object,
    key: str,
    edge_at: str,
    profile_at: str,
) -> object:
    edge_child = edge_value if isinstance(edge_value, Layered) else None
    profile_child = profile_value if isinstance(profile_value, Layered) else None
    model = profile_child if profile_child is not None else edge_child
    if model is None:
        return MISSING
    return _build(
        _view_class(model),
        cast("Layered[Edge] | None", edge_child),
        cast("Layered[Profile] | None", profile_child),
        _join(edge_at, key),
        _join(profile_at, key),
    )


def _children(
    cls: type[Layered[Any]],
    name: str,
    edge_value: object,
    profile_value: object,
    key: str,
    edge_at: str,
    profile_at: str,
) -> list[object]:
    spec = get_key_spec(cls.model_fields[name])
    if spec is None:
        raise TypeError(
            f"{cls.__name__}.{name}: a list of Layered models is matched between "
            + "the edge layer and the profile layer by key, and this list has "
            + 'none: declare it with KeyField, as in Annotated[list[Seg[L]], KeyField("segment.name", '
            + 'alt="segment.segment_id")]'
        )
    edge_items = _layered_items(edge_value)
    profile_items = _layered_items(profile_value)
    waiting: dict[object, list[int]] = {}
    for position, item in enumerate(edge_items):
        waiting.setdefault(item_identity(item, spec.paths, spec.alt), []).append(
            position
        )
    edge_base = _join(edge_at, key)
    profile_base = _join(profile_at, key)
    built: list[object] = []
    matched: set[int] = set()
    for position, item in enumerate(profile_items):
        counterpart = waiting.get(item_identity(item, spec.paths, spec.alt))
        index = counterpart.pop(0) if counterpart else None
        if index is not None:
            matched.add(index)
        built.append(
            _build(
                _view_class(item),
                None if index is None else edge_items[index],
                item,
                "" if index is None else f"{edge_base}[{index}]",
                f"{profile_base}[{position}]",
            )
        )
    for index, item in enumerate(edge_items):
        if index not in matched:
            built.append(
                _build(_view_class(item), item, None, f"{edge_base}[{index}]", "")
            )
    return built


def _layered_items(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in cast("list[object]", value) if isinstance(item, Layered)]


# ---------------------------------------------------------------------------
# The dump
# ---------------------------------------------------------------------------


def effective_dump(
    view: Layered[Effective],
) -> tuple[JsonObject, dict[str, Resolved[object]]]:
    """The view as wire JSON, and the ``Resolved`` of each wire path in it.

    The JSON has the wire keys (aliases) and the values as JSON mode writes
    them; a key is absent where the view has ``MISSING`` (an ``Unconverted``
    field), or a ``Resolved`` of it (an ``EdgeOnly`` or ``ProfileOnly`` field
    whose layer holds none, and has no default). A stored value (``EDGE`` or ``PROFILE``) is written as stored: the
    fields of a model it was not given are left out, which is what the server
    composes from the same data. A display default (``FACTORY``) is written
    whole. Each ``Resolved`` is written as its value. The map has an entry for
    each ``Resolved`` of the view, an item of an ``Additive`` list included,
    under its path in the view: dotted wire keys, a keyed ``Layered`` child as
    ``segments[<segmentId>]`` (the alt key; the key where the list has none)
    and any other list item as ``[<index>]``. That is not the ``Resolved``'s
    own ``source``, which is its place in the layer it came from.
    """
    found: dict[str, Resolved[object]] = {}
    return _dump_model(view, "", found), found


def _dump_model(
    view: Layered[Effective], prefix: str, found: dict[str, Resolved[object]]
) -> JsonObject:
    cls = type(view)
    data: JsonObject = {}
    for name, info in cls.model_fields.items():
        value: object = getattr(view, name)
        if value is MISSING:
            continue
        key = wire_name(cls, name)
        path = _join(prefix, key)
        if isinstance(value, Resolved):
            resolved = cast("Resolved[object]", value)
            if (
                resolved.value is MISSING
            ):  # an EdgeOnly or ProfileOnly field its layer lacks
                continue
            found[path] = resolved
            data[key] = _json(resolved.value, full=resolved.origin is Origin.FACTORY)
        elif isinstance(value, Layered):
            data[key] = _dump_model(cast("Layered[Effective]", value), path, found)
        elif isinstance(value, list):
            data[key] = _dump_list(
                cast("list[object]", value), path, get_key_spec(info), found
            )
        else:
            data[key] = _json(value)
    return data


def _dump_list(
    items: list[object],
    path: str,
    spec: KeySpec | None,
    found: dict[str, Resolved[object]],
) -> list[object]:
    dumped: list[object] = []
    for position, item in enumerate(items):
        if isinstance(item, Resolved):
            resolved = cast("Resolved[object]", item)
            found[f"{path}[{position}]"] = resolved
            dumped.append(_json(resolved.value, full=resolved.origin is Origin.FACTORY))
        elif isinstance(item, Layered):
            child = cast("Layered[Effective]", item)
            text = None if spec is None else _identity_text(child, spec)
            where = f"{path}[{position if text is None else text}]"
            dumped.append(_dump_model(child, where, found))
        else:
            dumped.append(_json(item))
    return dumped


def _identity_text(item: object, spec: KeySpec) -> str | None:
    """The text of *item*'s identity in its list: the alt key's value, else the key as ``item_key_text`` writes it; ``None`` for an item with neither."""
    kind, found = item_identity(item, spec.paths, spec.alt)
    if kind == "alt":
        return str(found)
    return item_key_text(item, spec.paths)


_ANY: TypeAdapter[object] = TypeAdapter(object)


def _json(value: object, *, full: bool = False) -> object:
    """*value* as JSON mode writes it; unless *full*, a model leaves out the fields it was not given (the stored data, not the defaults the model fills in)."""
    return _ANY.dump_python(value, mode="json", by_alias=True, exclude_unset=not full)
