"""Three-way JSON engine: apply the change ``before -> after`` to ``raw``.

``raw`` is a JSON document exactly as it was read. ``before`` is the wire dump
of the model parsed from it, and ``after`` the wire dump of that model now.
``rebase`` carries only the difference between ``before`` and ``after`` over to
``raw``, so what parsing lost (unknown keys, coerced values, defaults the dump
added) never reaches the result. ``raw_changes`` lists the differences between
two JSON documents, with the same addressing. ``find_lossy_assignments`` lists
the assignments to a model that ``rebase`` cannot carry over.

Locations are ``Path`` values: text for people, ``.parts`` for programs. A key
with spaces, dots or quotes in it is quoted, so a rendered path is never
ambiguous and ``Path.parse`` reads it back.

The optional *model* is the ``VcoModel`` class whose wire dump ``before`` and
``after`` are. It supplies two things that JSON alone cannot:

- the ``KeyField`` of each list, so keyed lists are matched by key and not by
  position. With ``model=None`` every list is unkeyed.
- the other keys a field is read from (``logging_enabled`` for
  ``loggingEnabled``), so a change lands on the key as read and does not add a
  second one.

The result of ``rebase`` may share sub-objects with its inputs. Callers copy.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Self, override

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import (
    VcoModel,
    _context_default_entry,  # pyright: ignore[reportPrivateUsage]
    _field_alias,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
    _is_mapping,  # pyright: ignore[reportPrivateUsage]
    _list_item_type,  # pyright: ignore[reportPrivateUsage]
)
from .fields import ConfigContext, KeySpec, get_key_spec, has_override_flag


@dataclass(frozen=True, slots=True)
class Key:
    """A keyed-list item, identified by its ``KeyField`` value."""

    value: str | int | float | bool


type PathPart = str | Key | int
"""``str`` is a field or object key, ``Key`` a keyed item, ``int`` a list position."""

_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BARE_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
_JSON_SCALAR = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|true|false|NaN|-?Infinity"
)
_INTEGER = re.compile(r"-?[0-9]+")
_POSITION = re.compile(r"[0-9]+")


def _is_bare_key(value: str) -> bool:
    """Whether a ``str`` key is written without quotes.

    Not when it would read as another JSON value: ``123`` (an ``int`` key),
    ``true``, ``1e-07`` and the like are quoted, so ``Key("true")`` and
    ``Key(True)`` render differently.
    """
    return (
        _BARE_KEY.fullmatch(value) is not None
        and not value.isdigit()
        and _JSON_SCALAR.fullmatch(value) is None
    )


def _render_key(value: str | float | bool) -> str:
    if isinstance(value, str) and _is_bare_key(value):
        return value
    return json.dumps(value)


def _render(parts: tuple[PathPart, ...]) -> str:
    out: list[str] = []
    for part in parts:
        if isinstance(part, Key):
            out.append(f"[{_render_key(part.value)}]")
        elif isinstance(part, str):
            name = part if _BARE_NAME.fullmatch(part) else json.dumps(part)
            out.append(f".{name}" if out else name)
        elif part < 0:
            raise ValueError(f"list position cannot be negative: {part}")
        else:
            out.append(f"[#{part}]")
    return "".join(out)


def _malformed(text: str, index: int, why: str) -> ValueError:
    return ValueError(f"malformed path {text!r} at {index}: {why}")


def _decode_string(text: str, index: int) -> tuple[str, int]:
    """The JSON string literal that starts at *index*, and where it ends."""
    try:
        decoded: tuple[object, int] = json.JSONDecoder().raw_decode(text, index)
    except ValueError:
        raise _malformed(text, index, "bad quoted string") from None
    value, end = decoded
    assert isinstance(value, str)  # it starts with a double quote
    return value, end


def _scalar(content: str) -> bool | int | float:
    """The value of text that ``_JSON_SCALAR`` matched."""
    if content in ("true", "false"):
        return content == "true"
    if _INTEGER.fullmatch(content):
        return int(content)
    return float(content)  # also NaN, Infinity


def _parse_name(text: str, index: int) -> tuple[str, int]:
    if text.startswith('"', index):
        return _decode_string(text, index)
    found = _BARE_NAME.match(text, index)
    if found is None:
        raise _malformed(text, index, "expected a name")
    return found.group(), found.end()


def _parse_bracket(text: str, index: int) -> tuple[PathPart, int]:
    """The part in the brackets that open at *index*, and where it ends."""
    start = index + 1
    if text.startswith('"', start):
        value, end = _decode_string(text, start)
        part: PathPart = Key(value)
    else:
        end = text.find("]", start)
        if end < 0:
            raise _malformed(text, index, "unclosed '['")
        content = text[start:end]
        if content.startswith("#"):
            if _POSITION.fullmatch(content, 1) is None:
                raise _malformed(text, start, "bad list position")
            part = int(content[1:])
        elif _JSON_SCALAR.fullmatch(content):
            part = Key(_scalar(content))
        elif _BARE_KEY.fullmatch(content) and not content.isdigit():
            part = Key(content)
        else:
            raise _malformed(text, start, "bad key")
    if not text.startswith("]", end):
        raise _malformed(text, end, "expected ']'")
    return part, end + 1


class Path(str):
    """A change location. Compares and hashes as its rendered text.

    ``.parts`` is the structure. Names are bare when they are identifiers and
    JSON strings otherwise, joined with ``.``; keyed items are ``[key]`` and
    list positions ``[#0]``: ``segments["Corp.Guest v2"].rules[r1].port``.
    """

    parts: tuple[PathPart, ...]

    def __new__(cls, parts: Iterable[PathPart]) -> Self:
        if isinstance(parts, str):
            raise TypeError("Path takes parts; use Path.parse() for text")
        parts = tuple(parts)
        self = super().__new__(cls, _render(parts))
        self.parts = parts
        return self

    @override
    def __getnewargs__(self) -> tuple[tuple[PathPart, ...]]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return (self.parts,)

    @classmethod
    def parse(cls, text: str) -> Self:
        """The path that renders as *text*. Raises ``ValueError`` if there is none."""
        parts: list[PathPart] = []
        index = 0
        while index < len(text):
            if text[index] == "[":
                part, index = _parse_bracket(text, index)
            else:
                if parts:
                    if text[index] != ".":
                        raise _malformed(text, index, "expected '.' or '['")
                    index += 1
                part, index = _parse_name(text, index)
            parts.append(part)
        return cls(parts)

    def child(self, part: PathPart) -> Self:
        return type(self)((*self.parts, part))


def _name(key: object) -> str:
    """An object key as a path name. JSON object keys are strings already."""
    return key if isinstance(key, str) else str(key)


@dataclass(frozen=True, slots=True, init=False)
class RawChange:
    """One difference between two JSON documents.

    ``MISSING`` stands for the side that has no value at ``path``. A *path* given
    as text is parsed.
    """

    path: Path
    before: object
    after: object

    def __init__(self, path: str, before: object, after: object) -> None:
        object.__setattr__(
            self, "path", path if isinstance(path, Path) else Path.parse(path)
        )
        object.__setattr__(self, "before", before)
        object.__setattr__(self, "after", after)


@dataclass(frozen=True, slots=True)
class _KeyedList:
    """A list whose items are matched by the value at ``key_path``."""

    spec: KeySpec
    """The list's ``KeyField``; its key has one part."""
    item: type[VcoModel]

    @property
    def key_path(self) -> str:
        """Dotted path of python field names to the key, as in ``KeyField``."""
        return self.spec.paths[0]


type _Node = type[VcoModel] | _KeyedList | None
"""What a JSON value is known to be: a model, a keyed list, or nothing (untyped)."""


# ---------------------------------------------------------------------------
# Typed navigation
# ---------------------------------------------------------------------------


def _model_field(node: _Node, key: object) -> tuple[str, FieldInfo] | None:
    """The field of a model node that JSON key *key* is read as, or None.

    Accepts every key the model reads a field from, not only the one it dumps.
    """
    if not isinstance(node, type) or not isinstance(key, str):
        return None
    name = node._alias_map().get(key)  # pyright: ignore[reportPrivateUsage]
    return None if name is None else (name, node.model_fields[name])


def _dump_key(node: _Node, key: object) -> object:
    """The key the model dumps the field read from *key* under (*key* if unknown)."""
    found = _model_field(node, key)
    return key if found is None else _field_alias(found[1], found[0])


def _child(node: _Node, key: object) -> _Node:
    """What the value under *key* is known to be.

    A keyed list for a ``KeyField`` list of models whose key has one part (of
    the first variant, for a discriminated union), a model class for a model
    field, else None.
    """
    found = _model_field(node, key)
    if found is None:
        return None
    field_info = found[1]
    spec = get_key_spec(field_info)
    item_type = _list_item_type(field_info)
    if spec is not None and len(spec.paths) == 1 and item_type is not None:
        return _KeyedList(spec, item_type.variants[0])
    return _field_model_type(field_info)


def _wire_keys(model: type[VcoModel], name: str) -> list[str]:
    """Every JSON key that field *name* of *model* is read from, its dump key first."""
    field_info = model.model_fields.get(name)
    if field_info is None:
        return [name]
    dump_key = _field_alias(field_info, name)
    others = (
        key
        for key, python_name in model._alias_map().items()  # pyright: ignore[reportPrivateUsage]
        if python_name == name and key != dump_key
    )
    return [dump_key, *others]


def _item_key(
    item: object, model: type[VcoModel], key_path: str
) -> str | int | float | None:
    """The key of a JSON *item* of *model* at *key_path*, or None if it has none.

    The path names python fields. Each step is read from any key the field is
    read from. A key that is absent, null or not a scalar is None.
    """
    current = item
    step_model: type[VcoModel] | None = model
    for part in key_path.split("."):
        if not _is_dict(current):
            return None
        candidates = [part] if step_model is None else _wire_keys(step_model, part)
        found = next((k for k in candidates if k in current), None)
        if found is None:
            return None
        current = current[found]
        field_info = None if step_model is None else step_model.model_fields.get(part)
        step_model = None if field_info is None else _field_model_type(field_info)
    return current if isinstance(current, (str, int, float)) else None


def _same_key(raw_key: object, parsed_key: object) -> bool:
    """Whether a key read from raw JSON is the key the parse produced from it.

    Tolerates coercion by the parse: raw ``"0"`` or ``0.0`` is parsed key ``0``.
    """
    return raw_key == parsed_key or str(raw_key) == str(parsed_key)


def _list_keys(items: list[object], node: _KeyedList) -> list[str | int | float] | None:
    """The key of every item, or None unless every item has one and they are distinct."""
    keys = [_item_key(item, node.item, node.key_path) for item in items]
    complete = [key for key in keys if key is not None]
    if len(complete) != len(keys) or len(set(complete)) != len(complete):
        return None
    return complete


# ---------------------------------------------------------------------------
# rebase
# ---------------------------------------------------------------------------


def rebase(
    raw: object,
    before: object,
    after: object,
    model: type[VcoModel] | None = None,
) -> object:
    """Apply the change ``before -> after`` to *raw*.

    - Where *before* and *after* agree, *raw* is kept, even where *raw* and
      *before* differ because parsing was lossy.
    - Where they differ at a leaf, or a key exists in only one, the *after* side
      is written to (or removed from) *raw*.
    - When a change needs a container that *raw* does not have, the whole
      container is taken from *after*.
    - A keyed list is matched by key across the three; item order comes from
      *after* and matched items recurse. A list without a key is replaced whole.
      So is a keyed list whose keys are missing or repeated, or whose length in
      *raw* differs from *before* (items cannot be paired up).

    ``rebase(raw, x, x, model) == raw`` for every *raw*.
    """
    return _rebase(raw, before, after, model)


def _rebase(raw: object, before: object, after: object, node: _Node) -> object:
    if before == after:
        return raw
    if _is_dict(raw) and _is_dict(before) and _is_dict(after):
        return _rebase_object(raw, before, after, node)
    if (
        isinstance(node, _KeyedList)
        and _is_list(raw)
        and _is_list(before)
        and _is_list(after)
    ):
        merged = _rebase_keyed_list(raw, before, after, node)
        if merged is not None:
            return merged
    return after


def _rebase_object(
    raw: dict[object, object],
    before: dict[object, object],
    after: dict[object, object],
    node: _Node,
) -> dict[object, object]:
    out: dict[object, object] = {}
    handled: set[object] = set()  # dump keys that the loop over raw dealt with
    for key, value in raw.items():
        # *before* and *after* use dump keys; *raw* may spell a field differently.
        dump_key = key if key in before or key in after else _dump_key(node, key)
        if dump_key not in before and dump_key not in after:
            out[key] = value  # the model does not know this key
            continue
        handled.add(dump_key)
        if dump_key not in after:
            continue  # removed by the caller
        if dump_key not in before:
            out[key] = after[dump_key]
        else:
            out[key] = _rebase(
                value, before[dump_key], after[dump_key], _child(node, dump_key)
            )
    for key, value in after.items():
        if key not in handled and (key not in before or before[key] != value):
            out[key] = value
    return out


def _rebase_keyed_list(
    raw: list[object], before: list[object], after: list[object], node: _KeyedList
) -> list[object] | None:
    """Rebase a keyed list item by item, or None when the items cannot be matched.

    *raw* and *before* are paired by position, which is only sound while they
    have the same length and the parse kept the order. A raw item whose own key
    is readable must therefore agree with the key of the *before* item at its
    position. *before* and *after* are paired by key.
    """
    if len(raw) != len(before):
        return None
    before_keys = _list_keys(before, node)
    after_keys = _list_keys(after, node)
    if before_keys is None or after_keys is None:
        return None
    for raw_item, before_key in zip(raw, before_keys, strict=True):
        raw_key = _item_key(raw_item, node.item, node.key_path)
        if raw_key is not None and not _same_key(raw_key, before_key):
            return None
    position = {key: i for i, key in enumerate(before_keys)}
    return [
        _rebase(raw[position[key]], before[position[key]], item, node.item)
        if key in position
        else item
        for key, item in zip(after_keys, after, strict=True)
    ]


# ---------------------------------------------------------------------------
# raw_changes
# ---------------------------------------------------------------------------


def raw_changes(
    old: object, new: object, model: type[VcoModel] | None = None
) -> list[RawChange]:
    """The differences between two JSON documents, each at the deepest path.

    Object keys are walked in *old* order, then those only in *new*. A keyed
    list whose items all have distinct keys on both sides is addressed as
    ``path[key]``; an item present on one side only is one change. Anything
    else that differs, including a list without a usable key and a keyed list
    whose common items changed order, is one change at its own path.
    """
    changes: list[RawChange] = []
    _collect_changes(changes, Path(()), old, new, model)
    return changes


def _collect_changes(
    out: list[RawChange], path: Path, old: object, new: object, node: _Node
) -> None:
    if old == new:
        return
    if _is_dict(old) and _is_dict(new):
        for key, old_value in old.items():
            if key in new:
                _collect_changes(
                    out,
                    path.child(_name(key)),
                    old_value,
                    new[key],
                    _child(node, key),
                )
            else:
                out.append(RawChange(path.child(_name(key)), old_value, MISSING))
        out.extend(
            RawChange(path.child(_name(key)), MISSING, new_value)
            for key, new_value in new.items()
            if key not in old
        )
        return
    if isinstance(node, _KeyedList) and _is_list(old) and _is_list(new):
        old_keys = _list_keys(old, node)
        new_keys = _list_keys(new, node)
        if old_keys is not None and new_keys is not None:
            _collect_keyed_changes(out, path, old, old_keys, new, new_keys, node)
            return
    out.append(RawChange(path, old, new))


def _collect_keyed_changes(
    out: list[RawChange],
    path: Path,
    old: list[object],
    old_keys: list[str | int | float],
    new: list[object],
    new_keys: list[str | int | float],
    node: _KeyedList,
) -> None:
    old_by_key = dict(zip(old_keys, old, strict=True))
    new_by_key = dict(zip(new_keys, new, strict=True))
    if [k for k in old_keys if k in new_by_key] != [
        k for k in new_keys if k in old_by_key
    ]:
        out.append(RawChange(path, old, new))  # reordered: only the list can say so
        return
    for key, old_item in old_by_key.items():
        if key in new_by_key:
            _collect_changes(
                out, path.child(Key(key)), old_item, new_by_key[key], node.item
            )
        else:
            out.append(RawChange(path.child(Key(key)), old_item, MISSING))
    out.extend(
        RawChange(path.child(Key(key)), MISSING, new_item)
        for key, new_item in new_by_key.items()
        if key not in old_by_key
    )


# ---------------------------------------------------------------------------
# find_lossy_assignments
# ---------------------------------------------------------------------------


def find_lossy_assignments(
    baseline: VcoModel,
    current: VcoModel,
    before: Mapping[str, object],
    after: Mapping[str, object],
    level: ConfigContext | None = None,
) -> list[Path]:
    """The assignments to *current* that ``rebase`` would drop, as python-name paths.

    *before* and *after* are the wire dumps of *baseline* and *current*, at
    config level *level* (default: the level of each model).
    ``rebase`` writes only what differs between them. A field the caller assigned
    (in ``current.model_fields_set``) that was not in the document as read (not
    in ``baseline.model_fields_set``) and whose dump is unchanged therefore never
    reaches the document: the caller assigned the default of an absent key.
    ``MISSING`` and ``None`` are not assignments of a value and do not count.

    The walk pairs *baseline* and *current* by field. A list is paired by key if
    it has a ``KeyField`` whose values are present and distinct in both dumps
    (items in only one are not paired). Any other list (unkeyed, or keyed with
    missing or repeated keys) is paired by position, and only if its dump is
    unchanged and the model lists have the same length: ``rebase`` keeps such a
    list as read when it is unchanged, but writes it whole from *after* when it
    changed, which includes every assignment inside it. The dumps are read at
    the same positions, so a dump whose lists do not line up with the model's
    is not walked. A field that is not in *baseline* and whose dump changed is
    written whole, so nothing inside it is lost.

    A field that is absent at the config level is not in the dumps at all, so
    ``rebase`` never sees it: a change to it is lost and its path is reported,
    unless the field is an ``OverrideFlag``. ``merge_patch`` sets those itself,
    when a sibling changes, so they are never the caller's assignment.

    Paths come in field order: ``segments[A].rules[r1].port``. Items of a list
    that is walked by position are ``rules[#0]``.
    """
    out: list[Path] = []
    _lossy_in_value(out, Path(()), baseline, current, before, after, None, level)
    return out


def is_override_flag(model: type[VcoModel], path: Path) -> bool:
    """Whether *path*, as ``find_lossy_assignments`` gives it, ends at an ``OverrideFlag`` of *model*.

    A path through an unkeyed list is not followed, so it is never one.
    """
    *parents, last = path.parts
    node: _Node = model
    for part in parents:
        if isinstance(part, str):
            node = _child(node, part)
        elif isinstance(node, _KeyedList):
            node = node.item
        else:
            return False
    found = _model_field(node, last)
    return found is not None and has_override_flag(found[1])


def _dump_get(dump: object, key: str) -> object:
    """``dump[key]``, or ``MISSING`` when *dump* is not an object or has no *key*."""
    return dump[key] if _is_mapping(dump) and key in dump else MISSING


def _lossy_in_value(
    out: list[Path],
    path: Path,
    baseline: object,
    current: object,
    before: object,
    after: object,
    node: _Node,
    level: ConfigContext | None,
) -> None:
    if isinstance(baseline, VcoModel):
        if isinstance(current, VcoModel) and type(current) is type(baseline):
            _lossy_in_model(out, path, baseline, current, before, after, level)
    elif _is_list(baseline) and _is_list(current):
        _lossy_in_list(out, path, baseline, current, before, after, node, level)


def _is_container(value: object) -> bool:
    return isinstance(value, VcoModel) or _is_list(value) or _is_dict(value)


def _is_unset_scalar(a: VcoModel, b: VcoModel, name: str) -> bool:
    """Whether neither model has field *name* set and it holds a scalar.

    Such a value is a default, and a default may be generated per parse
    (``default_factory=uuid.uuid4``), so two parses of the same document differ.
    """
    return (
        name not in a.model_fields_set
        and name not in b.model_fields_set
        and not _is_container(_field_value(b, name))
    )


def _same_value(a: object, b: object) -> bool:
    """Whether two model values hold the same data, whatever config level they carry.

    ``==`` on models also compares private state, which includes the level. A
    scalar that neither model has set is not compared (see ``_is_unset_scalar``).
    """
    if isinstance(a, VcoModel) and isinstance(b, VcoModel):
        return (
            type(a) is type(b)
            and _same_value(a.model_extra or {}, b.model_extra or {})
            and all(
                _same_value(_field_value(a, name), _field_value(b, name))
                for name in type(a).model_fields
                if not _is_unset_scalar(a, b, name)
            )
        )
    if _is_list(a) and _is_list(b):
        return len(a) == len(b) and all(
            _same_value(x, y) for x, y in zip(a, b, strict=True)
        )
    if _is_dict(a) and _is_dict(b):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
    return a == b


def _lossy_in_model(
    out: list[Path],
    path: Path,
    baseline: VcoModel,
    current: VcoModel,
    before: object,
    after: object,
    level: ConfigContext | None,
) -> None:
    field_level = level if level is not None else current.config_context
    for name, field_info in type(current).model_fields.items():
        dump_key = _field_alias(field_info, name)
        before_value = _dump_get(before, dump_key)
        after_value = _dump_get(after, dump_key)
        value = _field_value(current, name)
        field_path = path.child(name)
        if _context_default_entry(field_info, field_level) is MISSING:
            if not has_override_flag(field_info) and not _same_value(
                _field_value(baseline, name), value
            ):
                out.append(field_path)  # the dump omits it, so the change is lost
            continue
        if name not in baseline.model_fields_set:
            if before_value != after_value:
                continue  # rebase writes the field whole
            if (
                name in current.model_fields_set
                and value is not MISSING
                and value is not None
            ):
                out.append(field_path)
                continue
        _lossy_in_value(
            out,
            field_path,
            _field_value(baseline, name),
            value,
            before_value,
            after_value,
            _child(type(current), dump_key),
            level,
        )


def _keyed_pairs(
    before: list[object], after: list[object], node: _Node
) -> list[tuple[Key, int, int]] | None:
    """The items a keyed list has on both sides, as (key, index in *before*, index in *after*).

    None unless *node* is a keyed list whose keys are present and distinct in
    both dumps. Items in only one of them are not paired.
    """
    if not isinstance(node, _KeyedList):
        return None
    before_keys = _list_keys(before, node)
    after_keys = _list_keys(after, node)
    if before_keys is None or after_keys is None:
        return None
    position = {key: i for i, key in enumerate(before_keys)}
    return [
        (Key(key), position[key], i)
        for i, key in enumerate(after_keys)
        if key in position
    ]


def _lossy_in_list(
    out: list[Path],
    path: Path,
    baseline: list[object],
    current: list[object],
    before: object,
    after: object,
    node: _Node,
    level: ConfigContext | None,
) -> None:
    if not (_is_list(before) and _is_list(after)):
        return
    if len(before) != len(baseline) or len(after) != len(current):
        return  # the dump's positions are not the model's
    # (item path, baseline index, current index)
    pairs: list[tuple[Path, int, int]]
    keyed = _keyed_pairs(before, after, node)
    if keyed is not None:
        pairs = [(path.child(key), base, cur) for key, base, cur in keyed]
    else:
        # Items cannot be matched by key. A changed list is written whole.
        if before != after or len(baseline) != len(current):
            return
        pairs = [(path.child(i), i, i) for i in range(len(baseline))]
    for item_path, base_index, cur_index in pairs:
        _lossy_in_value(
            out,
            item_path,
            baseline[base_index],
            current[cur_index],
            before[base_index],
            after[cur_index],
            None,
            level,
        )


# ---------------------------------------------------------------------------
# carry_unset_defaults
# ---------------------------------------------------------------------------


def carry_unset_defaults(
    baseline: VcoModel,
    current: VcoModel,
    before: Mapping[str, object],
    after: dict[str, object],
) -> None:
    """Make *after* agree with *before* on every scalar that neither model has set.

    Some defaults are made anew on each parse (``default_factory=uuid.uuid4``),
    so *baseline* and *current* differ in them although nobody assigned them.
    In the dumps that reads as a change by the caller, and ``rebase`` would write
    it. Here *after* is edited, in place, so it does not: such a field takes the
    value it has in *before*. The models are left as they are.

    The walk pairs *baseline* and *current* as ``find_lossy_assignments`` does,
    except that a list without usable keys is paired by position whether or not
    its dump changed. Only a pair has a *before* value to carry.
    """
    _carry_in_value(baseline, current, before, after, None)


def _carry_in_value(
    baseline: object,
    current: object,
    before: object,
    after: object,
    node: _Node,
) -> None:
    if isinstance(baseline, VcoModel):
        if isinstance(current, VcoModel) and type(current) is type(baseline):
            _carry_in_model(baseline, current, before, after)
    elif _is_list(baseline) and _is_list(current):
        _carry_in_list(baseline, current, before, after, node)


def _carry_in_model(
    baseline: VcoModel, current: VcoModel, before: object, after: object
) -> None:
    if not (_is_dict(before) and _is_dict(after)):
        return
    for name, field_info in type(current).model_fields.items():
        dump_key = _field_alias(field_info, name)
        if dump_key not in before or dump_key not in after:
            continue
        if _is_unset_scalar(baseline, current, name):
            after[dump_key] = before[dump_key]
            continue
        _carry_in_value(
            _field_value(baseline, name),
            _field_value(current, name),
            before[dump_key],
            after[dump_key],
            _child(type(current), dump_key),
        )


def _carry_in_list(
    baseline: list[object],
    current: list[object],
    before: object,
    after: object,
    node: _Node,
) -> None:
    if not (_is_list(before) and _is_list(after)):
        return
    if len(before) != len(baseline) or len(after) != len(current):
        return  # the dump's positions are not the model's
    keyed = _keyed_pairs(before, after, node)
    if keyed is not None:
        pairs = [(base, cur) for _, base, cur in keyed]
    elif len(baseline) == len(current):
        pairs = [(i, i) for i in range(len(baseline))]
    else:
        return
    for base_index, cur_index in pairs:
        _carry_in_value(
            baseline[base_index],
            current[cur_index],
            before[base_index],
            after[cur_index],
            None,
        )
