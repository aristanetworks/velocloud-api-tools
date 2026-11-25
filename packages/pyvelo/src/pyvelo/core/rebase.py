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

- the ``KeyField`` of each list, so the items of a keyed list are paired by
  alt key, then by key (``pair_items``), and not by position. With
  ``model=None`` every list is unkeyed.
- the other keys a field is read from (``logging_enabled`` for
  ``loggingEnabled``), so a change lands on the key as read and does not add a
  second one.

The result of ``rebase`` may share sub-objects with its inputs. Callers copy.
"""

import json
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Self, get_args, get_origin, override

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import (
    VcoModel,
    _context_default_entry,  # pyright: ignore[reportPrivateUsage]
    _field_alias,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _held_type,  # pyright: ignore[reportPrivateUsage]
    _is_mapping,  # pyright: ignore[reportPrivateUsage]
    _is_vco_model_type,  # pyright: ignore[reportPrivateUsage]
    _members_without_missing_and_none,  # pyright: ignore[reportPrivateUsage]
    is_dict,
    is_list,
    list_item_type,
    python_name,
    ref_only_fields,
)
from .fields import ConfigContext, KeySpec, get_key_spec, has_override_flag
from .layers import Kind, field_kind
from .owned import unwrap

type KeyScalar = str | int | float | bool
"""One part of a key."""
type KeyValue = KeyScalar | tuple[KeyScalar, ...]
"""A key: one part, or the tuple of the parts of a composite key."""


@dataclass(frozen=True, slots=True)
class Key:
    """A keyed-list item, identified by its ``KeyField`` value.

    The value is a tuple for a composite key.
    """

    value: KeyValue


type PathPart = str | Key | int
"""``str`` is a field or object key, ``Key`` a keyed item, ``int`` a list position."""

_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BARE_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
_JSON_SCALAR = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|true|false|NaN|-?Infinity"
)
_INTEGER = re.compile(r"-?[0-9]+")
_POSITION = re.compile(r"[0-9]+")
_UNQUOTED_PART = re.compile(r'[^,\]"]+')


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


def _render_key(value: KeyScalar) -> str:
    if isinstance(value, str) and _is_bare_key(value):
        return value
    return json.dumps(value)


def key_text(value: KeyValue) -> str:
    """A key as ``Path`` writes it inside the brackets.

    One part is bare when it can be, JSON otherwise; the parts of a composite
    key are each so written, joined by commas:
    ``"10.0.0.0",24,"192.168.1.1",GE3``. ``parse_key_text`` reads it back.
    """
    if isinstance(value, tuple):
        return ",".join(_render_key(part) for part in value)
    return _render_key(value)


type KeyForm = str | tuple[str, ...]
"""A key in string form: how keys are compared (``"10"`` is the key ``10``)."""


def key_form(key: KeyValue | None) -> KeyForm | None:
    """*key* in string form, part by part for a composite key; None for no key."""
    if key is None:
        return None
    if isinstance(key, tuple):
        return tuple(str(part) for part in key)
    return str(key)


def key_part(value: object) -> KeyScalar:
    """One part of a key: a scalar as it is.

    Anything else (a ``UUID``) is in string form.
    """
    return value if isinstance(value, (str, int, float)) else str(value)


def item_key(item: object, paths: tuple[str, ...]) -> KeyValue | None:
    """The key of *item*, a model item of a keyed list, at the ``KeyField`` *paths*.

    The value at the one dotted path, or the tuple of the values at several (a
    composite key), each part as ``key_part`` gives it. ``""`` is a part. None
    when there are no paths, or a part, or a step to it, is absent, null or
    ``MISSING``: the item has no key.
    """
    parts: list[KeyScalar] = []
    for path in paths:
        value: object = item
        for name in path.split("."):
            if value is None or value is MISSING:
                return None
            value = unwrap(getattr(value, name, None))
        if value is None or value is MISSING:
            return None
        parts.append(key_part(value))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else tuple(parts)


def item_key_text(item: object, paths: tuple[str, ...]) -> str | None:
    """The key of a model item (``item_key``) as text; None when it has none.

    A one-part key in string form (``Global Segment``); a composite key as
    ``key_text`` writes it (``"10.0.0.0",24,"192.168.1.1",GE3``). This is for
    showing a key; keys are compared with ``key_form`` (``24`` and ``"24"``
    are one part, though their texts differ).
    """
    key = item_key(item, paths)
    if key is None:
        return None
    return key_text(key) if isinstance(key, tuple) else str(key)


def _render(parts: tuple[PathPart, ...]) -> str:
    out: list[str] = []
    for part in parts:
        if isinstance(part, Key):
            out.append(f"[{key_text(part.value)}]")
        elif isinstance(part, str):
            name = part if _BARE_NAME.fullmatch(part) else json.dumps(part)
            out.append(f".{name}" if out else name)
        elif part < 0:
            raise ValueError(f"list position cannot be negative: {part}")
        else:
            out.append(f"[#{part}]")
    return "".join(out)


def _malformed(text: str, index: int, why: str, what: str = "path") -> ValueError:
    return ValueError(f"malformed {what} {text!r} at {index}: {why}")


def _decode_string(text: str, index: int, what: str = "path") -> tuple[str, int]:
    """The JSON string literal that starts at *index*, and where it ends."""
    try:
        decoded: tuple[object, int] = json.JSONDecoder().raw_decode(text, index)
    except ValueError:
        raise _malformed(text, index, "bad quoted string", what) from None
    value, end = decoded
    if not isinstance(value, str):  # the caller found a double quote here
        raise _malformed(text, index, "not a quoted string", what)
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


def _parse_key_part(text: str, index: int, what: str) -> tuple[KeyScalar, int]:
    """The one key part that starts at *index*, and where it ends."""
    if text.startswith('"', index):
        return _decode_string(text, index, what)
    found = _UNQUOTED_PART.match(text, index)
    content = "" if found is None else found.group()
    value: KeyScalar
    if _JSON_SCALAR.fullmatch(content):
        value = _scalar(content)
    elif _BARE_KEY.fullmatch(content) and not content.isdigit():
        value = content
    else:
        raise _malformed(text, index, "bad key", what)
    return value, index + len(content)


def _parse_key_parts(
    text: str, index: int, what: str
) -> tuple[tuple[KeyScalar, ...], int]:
    """The comma-separated key parts that start at *index*, and where they end."""
    parts: list[KeyScalar] = []
    while True:
        part, index = _parse_key_part(text, index, what)
        parts.append(part)
        if not text.startswith(",", index):
            return tuple(parts), index
        index += 1


def parse_key_text(text: str) -> tuple[KeyScalar, ...]:
    """The parts of the key that ``key_text`` writes as *text*.

    There is one part for a single key.

    Raises ``ValueError`` if *text* is not what ``key_text`` writes.
    """
    parts, end = _parse_key_parts(text, 0, "key")
    if end != len(text):
        raise _malformed(text, end, "expected ',' or the end", "key")
    return parts


def _parse_bracket(text: str, index: int) -> tuple[PathPart, int]:
    """The part in the brackets that open at *index*, and where it ends."""
    start = index + 1
    part: PathPart
    if text.startswith("#", start):
        end = text.find("]", start)
        if end < 0:
            raise _malformed(text, index, "unclosed '['")
        if _POSITION.fullmatch(text, start + 1, end) is None:
            raise _malformed(text, start, "bad list position")
        part = int(text[start + 1 : end])
    else:
        parts, end = _parse_key_parts(text, start, "path")
        part = Key(parts[0] if len(parts) == 1 else parts)
    if not text.startswith("]", end):
        raise _malformed(text, end, "expected ']'")
    return part, end + 1


class Path(str):
    """A change location. Compares and hashes as its rendered text.

    ``.parts`` is the structure. Names are bare when they are identifiers and
    JSON strings otherwise, joined with ``.``; keyed items are ``[key]`` (the
    key as ``key_text`` writes it, so a composite key is
    ``["10.0.0.0",24,"192.168.1.1",GE3]``) and list positions ``[#0]``:
    ``segments["Corp.Guest v2"].rules[r1].port``.
    """

    __slots__: ClassVar[tuple[str, ...]] = ("parts",)
    parts: tuple[PathPart, ...]

    def __new__(cls, parts: Iterable[PathPart]) -> Self:
        """The path of *parts*; ``TypeError`` for text, which ``Path.parse`` reads."""
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
        """This path with *part* appended."""
        return type(self)((*self.parts, part))


def _name(key: object) -> str:
    """An object key as a path name. JSON object keys are strings already."""
    return key if isinstance(key, str) else str(key)


type ItemPart = Key | int
"""How a keyed-list item is named.

It is named by its key, or by its position where the key repeats or is absent.
"""


def item_text(part: ItemPart) -> str:
    """An item's name as text.

    It is the key as ``Path`` writes it, or ``#n`` for a position.
    """
    if isinstance(part, Key):
        return key_text(part.value)
    return f"#{part}"


@dataclass(frozen=True, slots=True)
class ItemPlace:
    """Where an item of a list goes: after the item named *after*, or first."""

    after: ItemPart | None = None

    @override
    def __str__(self) -> str:
        if self.after is None:
            return "first"
        return f"after {item_text(self.after)}"


@dataclass(frozen=True, slots=True)
class ItemMove:
    """An item of a list that changes place, and where it goes."""

    item: ItemPart
    place: ItemPlace

    @override
    def __str__(self) -> str:
        return f"{item_text(self.item)} {self.place}"


@dataclass(frozen=True, slots=True, init=False)
class RawChange:
    """One difference between two JSON documents.

    ``MISSING`` stands for the side that has no value at ``path``. A *path* given
    as text is parsed.

    *place* is where an item added to a keyed list goes. *moves* is set on the
    order entry of a keyed list (``is_order``): the items that change place,
    each with its place; the entry's values are lists of item names.
    """

    path: Path
    before: object
    after: object
    place: ItemPlace | None
    moves: tuple[ItemMove, ...] | None

    def __init__(
        self,
        path: str,
        before: object,
        after: object,
        *,
        place: ItemPlace | None = None,
        moves: tuple[ItemMove, ...] | None = None,
    ) -> None:
        """*path* may be a ``Path`` or its text, which is parsed."""
        object.__setattr__(
            self, "path", path if isinstance(path, Path) else Path.parse(path)
        )
        object.__setattr__(self, "before", before)
        object.__setattr__(self, "after", after)
        object.__setattr__(self, "place", place)
        object.__setattr__(self, "moves", moves)

    @property
    def is_order(self) -> bool:
        """Whether this is the order entry of a keyed list."""
        return self.moves is not None


@dataclass(frozen=True, slots=True)
class KeyedList:
    """A list whose items are paired by alt key, then by key (``pair_items``)."""

    spec: KeySpec
    """The list's ``KeyField``: the key's paths, one per part, and its alt key."""
    item: type[VcoModel]


@dataclass(frozen=True, slots=True)
class ModelMap:
    """An object whose every value is the model *value* (a ``dict[K, Model]`` field)."""

    value: type[VcoModel]


type Node = type[VcoModel] | KeyedList | ModelMap | None
"""What a JSON value is known to be.

It is a model, a keyed list, a map of models, or nothing (untyped).
"""


# ---------------------------------------------------------------------------
# Typed navigation
# ---------------------------------------------------------------------------


def node_field(node: Node, key: object) -> tuple[str, FieldInfo] | None:
    """The field of a model node that JSON key *key* is read as, or None.

    Accepts every key the model reads a field from, not only the one it dumps.
    """
    if not isinstance(node, type) or not isinstance(key, str):
        return None
    name = python_name(node, key)
    return None if name is None else (name, node.model_fields[name])


def _dump_key(node: Node, key: object) -> object:
    """The key the model dumps the field read from *key* under (*key* if unknown)."""
    found = node_field(node, key)
    return key if found is None else _field_alias(found[1], found[0])


def _map_value_type(field_info: FieldInfo) -> type[VcoModel] | None:
    """The model class of the values of a ``dict[K, Model]`` field, or None.

    ``MISSING`` and ``None`` are set aside, as ``list_kind`` sets them aside.
    None for any other field, and when the values hold no model or more than one.
    Of a union with several ``dict[...]`` members the first is taken and the
    others are not looked at.
    """
    for member in _members_without_missing_and_none(_held_type(field_info)):
        args = get_args(member)
        if get_origin(member) is not dict or len(args) != 2:  # noqa: PLR2004  # dict[K, V] has two type arguments
            continue
        models = [
            m
            for m in _members_without_missing_and_none(args[1])
            if _is_vco_model_type(m)
        ]
        if len(models) == 1:
            return models[0]
    return None


def node_child(node: Node, key: object) -> Node:
    """What the value under *key* is known to be.

    Whatever the key, the model of a map of models' values. Otherwise a keyed
    list for a ``KeyField`` list of models (of the first variant, for a
    discriminated union), a map of models for a ``dict[K, Model]`` field, a
    model class for a model field, else None.
    """
    if isinstance(node, ModelMap):
        return node.value
    found = node_field(node, key)
    if found is None:
        return None
    field_info = found[1]
    spec = get_key_spec(field_info)
    item_type = list_item_type(field_info)
    if spec is not None and item_type is not None:
        return KeyedList(spec, item_type.variants[0])
    map_value = _map_value_type(field_info)
    if map_value is not None:
        return ModelMap(map_value)
    return _field_model_type(field_info)


def wire_keys(model: type[VcoModel], name: str) -> list[str]:
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


def read_path(item: object, model: type[VcoModel], path: str) -> KeyScalar | None:
    """The scalar at dotted *path* of a JSON *item* of *model*, or None if it has none.

    The path names python fields. Each step is read from any key the field is
    read from. A value that is absent, null or not a scalar is None.
    """
    current = item
    step_model: type[VcoModel] | None = model
    for part in path.split("."):
        if not is_dict(current):
            return None
        candidates = [part] if step_model is None else wire_keys(step_model, part)
        found = next((k for k in candidates if k in current), None)
        if found is None:
            return None
        current = current[found]
        field_info = None if step_model is None else step_model.model_fields.get(part)
        step_model = None if field_info is None else _field_model_type(field_info)
    return current if isinstance(current, (str, int, float)) else None


def _json_item_key(
    item: object, model: type[VcoModel], paths: tuple[str, ...]
) -> KeyValue | None:
    """The key of a JSON *item* of *model*, or None if it has none.

    The value at the one path of *paths*, or the tuple of the values at
    several (a composite key). None unless every part can be read
    (``read_path``). ``item_key`` is the same for a model item.
    """
    parts: list[KeyScalar] = []
    for path in paths:
        part = read_path(item, model, path)
        if part is None:
            return None
        parts.append(part)
    return parts[0] if len(parts) == 1 else tuple(parts)


def _same_key(raw_key: KeyValue, parsed_key: KeyValue) -> bool:
    """Whether a key read from raw JSON is the key the parse produced from it.

    Tolerates coercion by the parse: raw ``"0"`` or ``0.0`` is parsed key
    ``0``, and a raw key in another letter case is the same key (an
    upper-case UUID string is parsed as a ``UUID``, which is lower case). A
    composite key is compared part by part.

    This compares a raw item with its own parse at the same position, so a
    difference of case can only be the parse's. It is not how keys of
    different items are compared: selectors and ``pair_items`` compare keys
    exactly, in string form.
    """
    if isinstance(raw_key, tuple) or isinstance(parsed_key, tuple):
        return (
            isinstance(raw_key, tuple)
            and isinstance(parsed_key, tuple)
            and len(raw_key) == len(parsed_key)
            and all(_same_key(r, p) for r, p in zip(raw_key, parsed_key, strict=True))
        )
    if raw_key == parsed_key:
        return True
    raw_text, parsed_text = str(raw_key), str(parsed_key)
    return raw_text == parsed_text or raw_text.casefold() == parsed_text.casefold()


def _keys(items: list[object], node: KeyedList) -> list[KeyValue | None]:
    """The key of each item, None for an item without one."""
    return [_json_item_key(item, node.item, node.spec.paths) for item in items]


def key_forms(items: list[object], node: KeyedList) -> list[KeyForm | None]:
    """The key of each item in string form, None for an item without one."""
    return [key_form(key) for key in _keys(items, node)]


def _unique(keys: list[KeyForm | None]) -> set[KeyForm]:
    """The keys that occur exactly once in *keys*."""
    counts = Counter(key for key in keys if key is not None)
    return {key for key, count in counts.items() if count == 1}


@dataclass(frozen=True, slots=True)
class Pairing:
    """How the items of a keyed list *old* became the items of *new*.

    It is what ``pair_items`` gives.
    """

    pairs: tuple[tuple[int, int], ...]
    """(index in *old*, index in *new*) of each item on both sides, in *new* order."""
    added: tuple[int, ...]
    """Indexes in *new* of the items only *new* has."""
    removed: tuple[int, ...]
    """Indexes in *old* of the items only *old* has."""


def pair_unique(
    old_values: list[KeyForm | None],
    new_values: list[KeyForm | None],
    old_left: list[int],
    new_left: list[int],
    pairs: dict[int, int],
) -> None:
    """Pair the items left whose value occurs once among them on each side.

    The values are in string form. The pairs go into *pairs*, as index in new
    to index in old. None is no value.
    """
    old_unique = _unique([old_values[i] for i in old_left])
    new_unique = _unique([new_values[j] for j in new_left])
    old_at = {old_values[i]: i for i in old_left if old_values[i] in old_unique}
    for j in new_left:
        value = new_values[j]
        if value in new_unique and value in old_at:
            pairs[j] = old_at[value]


def pair_items(
    old: list[object],
    new: list[object],
    node: KeyedList,
    *,
    old_alt_read: Sequence[bool] | None = None,
) -> Pairing | None:
    """Pair the items of keyed list *old* with those of *new*.

    None means the list is replaced whole.

    First by alt key, for each alt-key value that occurs once on each side;
    then the items left by key, for each key that occurs once on each side
    among them. Keys and alt keys are compared in string form, part by part
    for a composite key, so ``24`` as dumped and ``"24"`` as read are one.
    Items of *new* left over are added, and items of *old* left over removed.
    An item of *old* left over is removed when it can be identified: by a key
    no other item of *old* has, or by an alt key that was read and that no
    other item of *old* has. When one cannot, what became of it cannot be
    told, and the result is None.

    *old_alt_read*, one flag per item of *old*, says whose alt key was read
    from the document; the others are taken to have none. An alt key the parse
    made up (a generated default) names nothing, though it may have been copied
    to *new* (``carry_unset_defaults``). Default: every alt key was read.
    """
    old_keys, new_keys = key_forms(old, node), key_forms(new, node)
    pairs: dict[int, int] = {}  # index in new -> index in old
    old_left, new_left = list(range(len(old))), list(range(len(new)))
    alt = node.spec.alt
    old_alts: list[KeyForm | None] = [None] * len(old)
    if alt is not None:
        old_alts = [
            key_form(read_path(item, node.item, alt))
            if old_alt_read is None or old_alt_read[i]
            else None
            for i, item in enumerate(old)
        ]
        pair_unique(
            old_alts,
            [key_form(read_path(item, node.item, alt)) for item in new],
            old_left,
            new_left,
            pairs,
        )
        paired = set(pairs.values())
        old_left = [i for i in old_left if i not in paired]
        new_left = [j for j in new_left if j not in pairs]
    pair_unique(old_keys, new_keys, old_left, new_left, pairs)
    paired = set(pairs.values())
    removed = tuple(i for i in old_left if i not in paired)
    unique_keys, unique_alts = _unique(old_keys), _unique(old_alts)
    if any(
        old_keys[i] not in unique_keys and old_alts[i] not in unique_alts
        for i in removed
    ):
        return None
    return Pairing(
        pairs=tuple((pairs[j], j) for j in sorted(pairs)),
        added=tuple(j for j in new_left if j not in pairs),
        removed=removed,
    )


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
    - The items of a keyed list in *before* and *after* are paired by alt key,
      then by key (``pair_items``), so a renamed item stays one item; item
      order comes from *after*, paired items recurse and the others are taken
      from *after*. A list without a key is replaced whole. So is a keyed list
      that ``pair_items`` cannot pair, or whose length in *raw* differs from
      *before* (raw items cannot be paired up with *before*'s).
    - The values of a map of models (a ``dict[K, Model]`` field) are rebased
      entry by entry, each as its model, so a change lands on the key as read
      and a keyed list below the map is paired.

    ``rebase(raw, x, x, model) == raw`` for every *raw*.
    """
    return _rebase(raw, before, after, model)


def _rebase(raw: object, before: object, after: object, node: Node) -> object:
    if before == after:
        return raw
    if is_dict(raw) and is_dict(before) and is_dict(after):
        return _rebase_object(raw, before, after, node)
    if (
        isinstance(node, KeyedList)
        and is_list(raw)
        and is_list(before)
        and is_list(after)
    ):
        merged = _rebase_keyed_list(raw, before, after, node)
        if merged is not None:
            return merged
    return after


def _rebase_object(
    raw: dict[object, object],
    before: dict[object, object],
    after: dict[object, object],
    node: Node,
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
                value, before[dump_key], after[dump_key], node_child(node, dump_key)
            )
    out.update(
        {
            key: value
            for key, value in after.items()
            if key not in handled and (key not in before or before[key] != value)
        }
    )
    return out


def _alt_agrees(raw_alt: KeyScalar | None, before_alt: KeyScalar | None) -> bool:
    """Whether an alt key of *before* is the one the raw item has, so it was read.

    A raw item without one had it made up by the parse. Compared as
    ``_same_key`` compares, so letter case does not count.
    """
    return (
        raw_alt is not None
        and before_alt is not None
        and _same_key(raw_alt, before_alt)
    )


def _rebase_keyed_list(
    raw: list[object], before: list[object], after: list[object], node: KeyedList
) -> list[object] | None:
    """Rebase a keyed list item by item, or None when the items cannot be matched.

    *raw* and *before* are paired by position, which is only sound while they
    have the same length and the parse kept the order. A raw item whose own key
    is readable must therefore agree with the key of the *before* item at its
    position. *before* and *after* are paired by ``pair_items``, by the alt
    keys of *before* that the raw item at the same position has as well.
    """
    if len(raw) != len(before):
        return None
    for raw_item, before_key in zip(raw, _keys(before, node), strict=True):
        raw_key = _json_item_key(raw_item, node.item, node.spec.paths)
        if raw_key is not None and (
            before_key is None or not _same_key(raw_key, before_key)
        ):
            return None
    alt = node.spec.alt
    alt_read = (
        None
        if alt is None
        else [
            _alt_agrees(
                read_path(raw_item, node.item, alt),
                read_path(before_item, node.item, alt),
            )
            for raw_item, before_item in zip(raw, before, strict=True)
        ]
    )
    pairing = pair_items(before, after, node, old_alt_read=alt_read)
    if pairing is None:
        return None
    old_of = {new: old for old, new in pairing.pairs}
    return [
        _rebase(raw[old_of[j]], before[old_of[j]], item, node.item)
        if j in old_of
        else item
        for j, item in enumerate(after)
    ]


# ---------------------------------------------------------------------------
# raw_changes
# ---------------------------------------------------------------------------


def raw_changes(
    old: object, new: object, model: type[VcoModel] | None = None
) -> list[RawChange]:
    """The differences between two JSON documents, each at the deepest path.

    Object keys are walked in *old* order, then those only in *new*. The items
    of a keyed list that ``pair_items`` pairs are walked in *old* order, then
    those only in *new*. An item is addressed as ``path[key]``, by its key in
    *old* (in *new* for an item only *new* has), or as ``path[#n]``, by its
    position there, when that key is absent or not unique. An item present on
    one side only is one change; an item added to a keyed list has its
    ``place``. A keyed list whose items changed order has one more change, at
    its own path: the order entry, whose values are lists of item names and
    whose ``moves`` are the items that changed place. Anything else that
    differs, including a list without a usable key and a keyed list in which an
    added item would have the name of an item of *old*, is one change at its
    own path.
    """
    changes: list[RawChange] = []
    collect_changes(changes, Path(()), old, new, model)
    return changes


def collect_changes(
    out: list[RawChange], path: Path, old: object, new: object, node: Node
) -> None:
    """Append to *out* the changes from *old* to *new* under *path*.

    *node* is the model's shape at *path*; ``raw_changes`` describes the walk.
    """
    if old == new:
        return
    if is_dict(old) and is_dict(new):
        for key, old_value in old.items():
            if key in new:
                collect_changes(
                    out,
                    path.child(_name(key)),
                    old_value,
                    new[key],
                    node_child(node, key),
                )
            else:
                out.append(RawChange(path.child(_name(key)), old_value, MISSING))
        out.extend(
            RawChange(path.child(_name(key)), MISSING, new_value)
            for key, new_value in new.items()
            if key not in old
        )
        return
    if (
        isinstance(node, KeyedList)
        and is_list(old)
        and is_list(new)
        and _collect_keyed_changes(out, path, old, new, node)
    ):
        return
    out.append(RawChange(path, old, new))


def item_parts(items: list[object], node: KeyedList) -> list[ItemPart]:
    """The name of each item: its key, or its position where that is not unique."""
    keys = _keys(items, node)
    unique = _unique([key_form(key) for key in keys])
    return [
        Key(key) if key is not None and key_form(key) in unique else i
        for i, key in enumerate(keys)
    ]


def item_names(
    old: list[object], new: list[object], node: KeyedList, pairing: Pairing
) -> dict[int, ItemPart] | None:
    """The name of each item of *new*, by its index; None if two items would share one.

    An item that *old* has is named by its part there, also when it is renamed
    or moved; an added item by its part in *new*. None when an added item would
    be named as an item of *old*.
    """
    old_parts = item_parts(old, node)
    new_parts = item_parts(new, node)
    names: dict[int, ItemPart] = {j: old_parts[i] for i, j in pairing.pairs}
    names.update({j: new_parts[j] for j in pairing.added})
    if any(names[j] in old_parts for j in pairing.added):
        return None
    return names


def staying(order: Sequence[int]) -> set[int]:
    """The positions in *order* of a longest increasing run.

    They are the items that need not move.
    """
    # tails[k]: the position in order of the last value of the run of length k + 1
    tails: list[int] = []
    values: list[int] = []  # order[tails[k]]
    before: list[
        int
    ] = []  # before[n]: the position of the value before order[n] in its run, or -1
    for n, value in enumerate(order):
        k = bisect_left(values, value)
        before.append(tails[k - 1] if k else -1)
        if k == len(tails):
            tails.append(n)
            values.append(value)
        else:
            tails[k] = n
            values[k] = value
    kept: set[int] = set()
    n = tails[-1] if tails else -1
    while n >= 0:
        kept.add(n)
        n = before[n]
    return kept


def place_of(names: dict[int, ItemPart], j: int) -> ItemPlace:
    """The place of item *j* of the new list: after the item before it, or first."""
    return ItemPlace(names[j - 1] if j else None)


def _collect_keyed_changes(
    out: list[RawChange],
    path: Path,
    old: list[object],
    new: list[object],
    node: KeyedList,
) -> bool:
    """Add the changes of a keyed list item by item; False if they cannot be paired."""
    pairing = pair_items(old, new, node)
    if pairing is None:
        return False
    names = item_names(old, new, node, pairing)
    if names is None:
        return False
    new_of = dict(pairing.pairs)
    old_parts = item_parts(old, node)
    for i, old_item in enumerate(old):
        item_path = path.child(old_parts[i])
        if i in new_of:
            collect_changes(out, item_path, old_item, new[new_of[i]], node.item)
        else:
            out.append(RawChange(item_path, old_item, MISSING))
    out.extend(
        RawChange(path.child(names[j]), MISSING, new[j], place=place_of(names, j))
        for j in pairing.added
    )
    order = [i for i, _ in pairing.pairs]
    if order != sorted(order):
        kept = staying(order)
        out.append(
            RawChange(
                path,
                [item_text(old_parts[i]) for i in sorted(order)],
                [item_text(names[j]) for j in range(len(new))],
                moves=tuple(
                    ItemMove(names[j], place_of(names, j))
                    for n, (_, j) in enumerate(pairing.pairs)
                    if n not in kept
                ),
            )
        )
    return True


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

    The walk pairs *baseline* and *current* by field. The items of a list with
    a ``KeyField`` are paired by alt key, then by key, in the dumps, as
    ``rebase`` pairs them (``pair_items``; items in only one are not paired).
    Any other list (unkeyed, or keyed but not paired) is paired by position,
    and only if its dump is unchanged and the model lists have the same length:
    ``rebase`` keeps such a list as read when it is unchanged, but writes it
    whole from *after* when it changed, which includes every assignment inside
    it. The dumps are read at the same positions, so a dump whose lists do not
    line up with the model's is not walked. A field that is not in *baseline*
    and whose dump changed is written whole, so nothing inside it is lost.

    The values of any dict held on both sides are paired by key and each pair
    is walked: as its model for a map of models (a ``dict[K, Model]`` field),
    untyped for any other dict (a ``dict[str, list[str]]`` field), where it
    reports nothing. An entry only one side has is not walked: a new entry is
    written whole. The key is a name in the path:
    ``loopback_interfaces.LO1.advertise``.

    A field that is absent at the config level is not in the dumps at all, so
    ``rebase`` never sees it: a change to it is lost and its path is reported,
    unless the field is an ``OverrideFlag``. ``merge_patch`` sets those itself,
    when a sibling changes, so they are never the caller's assignment.

    Paths come in field order: ``segments[A].rules[r1].port``. An item is
    named by its key in *before*. Items of a list that is walked by position,
    and paired items whose key in *before* is not unique, are ``rules[#0]``.
    """
    out: list[Path] = []
    _lossy_in_value(out, Path(()), baseline, current, before, after, None, level)
    return out


def python_path(path: Path, model: type[VcoModel] | None) -> Path:
    """*path* with each field named as a patch of *model* names it.

    A path of ``raw_changes`` or of a plan names fields by the keys the VCO
    uses (``loggingEnabled``); a patch uses python names (``logging_enabled``).
    A name no field is found for is kept, so are a keyed item, a position and
    a key of a map of models. Below a list without a key, and for a *model* of
    None, nothing is renamed.
    """
    node: Node = model
    parts: list[PathPart] = []
    for part in path.parts:
        if isinstance(part, str):
            found = node_field(node, part)
            parts.append(part if found is None else found[0])
            node = node_child(node, part)
        else:
            parts.append(part)
            node = node.item if isinstance(node, KeyedList) else None
    return Path(parts)


def is_override_flag(model: type[VcoModel], path: Path) -> bool:
    """Whether *path* ends at an ``OverrideFlag`` of *model*.

    *path* is as ``find_lossy_assignments`` gives it.

    A path of ``raw_changes`` is accepted too: its names are keys as written
    (``bgp``, ``loopbackInterfaces``), and each is read through the model's
    accepted keys. A path through an unkeyed list is not followed, so it is
    never one. A name below a map of models is the map key:
    ``boxes.b1.ports[p1].override``.
    """
    *parents, last = path.parts
    node: Node = model
    for part in parents:
        if isinstance(part, str):
            node = node_child(node, part)
        elif isinstance(node, KeyedList):
            node = node.item
        else:
            return False
    found = node_field(node, last)
    return found is not None and has_override_flag(found[1])


def _dump_get(dump: object, key: str) -> object:
    """``dump[key]``, or ``MISSING`` when *dump* is not an object or has no *key*."""
    return dump[key] if _is_mapping(dump) and key in dump else MISSING


def _lossy_in_value(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    out: list[Path],
    path: Path,
    baseline: object,
    current: object,
    before: object,
    after: object,
    node: Node,
    level: ConfigContext | None,
) -> None:
    if isinstance(baseline, VcoModel):
        if isinstance(current, VcoModel) and type(current) is type(baseline):
            _lossy_in_model(out, path, baseline, current, before, after, level)
    elif is_list(baseline) and is_list(current):
        _lossy_in_list(out, path, baseline, current, before, after, node, level)
    elif is_dict(baseline) and is_dict(current):
        for key, value in current.items():
            if key not in baseline:
                continue  # an entry only *current* has is written whole
            # A JSON-mode dump writes every map key as a string.
            before_value = _dump_get(before, str(key))
            after_value = _dump_get(after, str(key))
            if before_value is MISSING or after_value is MISSING:
                continue
            _lossy_in_value(
                out,
                path.child(str(key)),
                baseline[key],
                value,
                before_value,
                after_value,
                None,
                level,
            )


def _is_container(value: object) -> bool:
    return isinstance(value, VcoModel) or is_list(value) or is_dict(value)


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
                _same_value(
                    unwrap(_field_value(a, name)), unwrap(_field_value(b, name))
                )
                for name in type(a).model_fields
                if not _is_unset_scalar(a, b, name)
            )
        )
    if is_list(a) and is_list(b):
        return len(a) == len(b) and all(
            _same_value(x, y) for x, y in zip(a, b, strict=True)
        )
    if is_dict(a) and is_dict(b):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
    return a == b


def _lossy_in_model(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    out: list[Path],
    path: Path,
    baseline: VcoModel,
    current: VcoModel,
    before: object,
    after: object,
    level: ConfigContext | None,
) -> None:
    field_level = level if level is not None else current.config_context
    ref_only = ref_only_fields(type(current))
    for name, field_info in type(current).model_fields.items():
        if name in ref_only:
            continue  # an edit of it is planned in the refs, not in the data
        dump_key = _field_alias(field_info, name)
        before_value = _dump_get(before, dump_key)
        after_value = _dump_get(after, dump_key)
        value = unwrap(_field_value(current, name))
        held = unwrap(_field_value(baseline, name))
        field_path = path.child(name)
        if _context_default_entry(field_info, field_level) is MISSING:
            if not has_override_flag(field_info) and not _same_value(held, value):
                out.append(field_path)  # the dump omits it, so the change is lost
            continue
        if field_kind(field_info).kind is Kind.INHERITABLE:
            # It can always express "not stored" (the edge stores nothing, the
            # profile leaves out a display default), so assigning it is never a
            # loss; what it holds is walked as any value is.
            _lossy_in_value(
                out,
                field_path,
                held,
                value,
                before_value,
                after_value,
                node_child(type(current), dump_key),
                level,
            )
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
            held,
            value,
            before_value,
            after_value,
            node_child(type(current), dump_key),
            level,
        )


def _was_read(item: object, path: str) -> bool:
    """Whether the value at dotted *path* of model *item* was read, not defaulted."""
    current = item
    for name in path.split("."):
        if not isinstance(current, VcoModel) or name not in current.model_fields_set:
            return False
        current = unwrap(_field_value(current, name))
    return True


def _keyed_pairs(
    baseline: list[object], before: list[object], after: list[object], node: Node
) -> list[tuple[PathPart, int, int]] | None:
    """The items a keyed list has on both sides.

    Each is (part, index in *before*, index in *after*).

    The part is the item's key in *before*, or its position there where that
    key is not unique. None unless *node* is a keyed list that ``pair_items``
    pairs, by the alt keys of *baseline*'s items that were read and not
    defaulted. Items in only one of the dumps are not paired.
    """
    if not isinstance(node, KeyedList):
        return None
    alt = node.spec.alt
    alt_read = None if alt is None else [_was_read(item, alt) for item in baseline]
    pairing = pair_items(before, after, node, old_alt_read=alt_read)
    if pairing is None:
        return None
    parts = item_parts(before, node)
    return [(parts[i], i, j) for i, j in pairing.pairs]


def _lossy_in_list(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    out: list[Path],
    path: Path,
    baseline: list[object],
    current: list[object],
    before: object,
    after: object,
    node: Node,
    level: ConfigContext | None,
) -> None:
    if not (is_list(before) and is_list(after)):
        return
    if len(before) != len(baseline) or len(after) != len(current):
        return  # the dump's positions are not the model's
    # (item path, baseline index, current index)
    pairs: list[tuple[Path, int, int]]
    keyed = _keyed_pairs(baseline, before, after, node)
    if keyed is not None:
        pairs = [(path.child(part), base, cur) for part, base, cur in keyed]
    else:
        # Items cannot be paired by key. A changed list is written whole.
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
    except that a list that cannot be paired by key is paired by position
    whether or not its dump changed. Only a pair has a *before* value to carry.
    An alt key made anew on each parse (``QosRule.rule_logical_id`` read from a
    rule without one) differs between the dumps, so such items pair by key;
    once carried, the dumps agree on it and ``rebase`` pairs them by it. The
    values of any dict held on both sides are paired by key, whether it is a map
    of models or another dict (walked untyped, so it carries nothing), and an
    entry only one side has is not walked.
    """
    _carry_in_value(baseline, current, before, after, None)


def _carry_in_value(
    baseline: object,
    current: object,
    before: object,
    after: object,
    node: Node,
) -> None:
    if isinstance(baseline, VcoModel):
        if isinstance(current, VcoModel) and type(current) is type(baseline):
            _carry_in_model(baseline, current, before, after)
    elif is_list(baseline) and is_list(current):
        _carry_in_list(baseline, current, before, after, node)
    elif is_dict(baseline) and is_dict(current):
        if not (is_dict(before) and is_dict(after)):
            return
        for key, value in current.items():
            # A JSON-mode dump writes every map key as a string.
            name = str(key)
            if key in baseline and name in before and name in after:
                _carry_in_value(baseline[key], value, before[name], after[name], None)


def _carry_in_model(
    baseline: VcoModel, current: VcoModel, before: object, after: object
) -> None:
    if not (is_dict(before) and is_dict(after)):
        return
    for name, field_info in type(current).model_fields.items():
        dump_key = _field_alias(field_info, name)
        if dump_key not in before or dump_key not in after:
            continue
        if _is_unset_scalar(baseline, current, name):
            after[dump_key] = before[dump_key]
            continue
        _carry_in_value(
            unwrap(_field_value(baseline, name)),
            unwrap(_field_value(current, name)),
            before[dump_key],
            after[dump_key],
            node_child(type(current), dump_key),
        )


def _carry_in_list(
    baseline: list[object],
    current: list[object],
    before: object,
    after: object,
    node: Node,
) -> None:
    if not (is_list(before) and is_list(after)):
        return
    if len(before) != len(baseline) or len(after) != len(current):
        return  # the dump's positions are not the model's
    keyed = _keyed_pairs(baseline, before, after, node)
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
