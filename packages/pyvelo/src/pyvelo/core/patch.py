"""The patch format of ``VcoModel.merge_patch``.

``PatchError`` is what a patch that cannot be applied raises, with the place in
the patch it failed at. ``add``, ``delete``, ``move`` and ``at`` write list item
operations as plain JSON values; ``parse_item_op`` reads one back and
``selector_of`` writes the selector of an item named by its key or position.
``checked_patch`` is the check every patch passes once, at entry.
"""

import datetime
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Literal, cast, override
from uuid import UUID

from pydantic import MISSING, BaseModel

from .rebase import (
    ItemPart,
    Key,
    KeyValue,
    Path,
    PathPart,
    key_form,
    key_text,
    parse_key_text,
)

MAX_PATCH_DEPTH = 64
"""How many levels of dicts and lists a patch may nest, the patch itself being the first."""


type PatchErrorKind = Literal[
    "not_json",
    "address",
    "unknown_field",
    "duplicate",
    "null",
    "invalid_value",
    "operation",
    "selector",
    "no_item",
    "several_items",
    "exists",
    "segment_rule",
    "fixed_list",
]
"""Which sort of refusal a ``PatchError`` is."""


class PatchError(ValueError):
    """A patch that cannot be applied. ``path`` is where in the patch it failed.

    ``kind`` says which sort of refusal it is; the patch:

    - ``not_json``: holds a value that is not JSON, a number that is not
      finite, or nests too deep.
    - ``address``: was sent, by the ``module``, ``segment`` or ``path`` of the
      call, to a place that takes no patch.
    - ``unknown_field``: has a key that names no field.
    - ``duplicate``: names one thing twice: a field by two keys, an item by two
      selectors, a key by two adds or by two items of a list value.
    - ``null``: has ``null`` where it means nothing: a field that cannot be
      unset, or an item's value.
    - ``invalid_value``: has a value, an item or a changed model that
      validation refuses, or a name that cannot be looked up.
    - ``operation``: has an item operation that is malformed, or that this
      list or map does not take.
    - ``selector``: has a selector or anchor that is not one for this list.
    - ``no_item``: has a selector or anchor that names no item.
    - ``several_items``: has a selector or anchor that names more than one item.
    - ``exists``: adds, or renames to, a key, alt key or value that is already
      there.
    - ``segment_rule``: breaks a rule of the ``segments`` lists.
    - ``fixed_list``: edits a fixed part of a ``FixedList``: adds, deletes,
      moves, renames or reorders its entries, or changes a field of an
      entry that is not editable.

    A pydantic ``ValidationError`` behind it is its ``__cause__``.
    """

    path: Path
    kind: PatchErrorKind

    def __init__(self, message: str, path: Path, kind: PatchErrorKind) -> None:
        super().__init__(message, path, kind)
        self.message: str = message
        self.path = path
        self.kind = kind

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path.parts else self.message


type Selector = str | int | UUID | tuple[object, ...]
"""A key or value (a ``UUID`` key too), ``at(n)``, or the tuple of the parts of a composite key."""

_POSITION_WORDS = ("first", "last")
_POSITION_ANCHORS = ("before", "after")
_POSITION_FORMS = (
    'a position is "first", "last", {"before": selector} or {"after": selector}'
)


def at(n: int) -> str:
    """The selector of the item at position ``n`` of the list as it was."""
    if n < 0:
        raise ValueError(f"a position counts from 0, not {n}")
    return f"#{n}"


def key_selector(key: KeyValue) -> str:
    """The selector that names an item by *key*: the simplest text a patch reads back as that key.

    A one-part key in string form. A composite key as its parts in string form
    joined by commas (``10.0.0.0,24,192.168.1.1,GE3``); in the form
    ``key_text`` writes where a part holds a comma or a double quote, or where
    the joined text would be read as another key (a part ``"true"``).
    """
    if not isinstance(key, tuple):
        return str(key)
    texts = [str(part) for part in key]
    if any("," in text or '"' in text for text in texts):
        return key_text(key)
    plain = ",".join(texts)
    try:
        written = parse_key_text(plain)
    except ValueError:
        return plain
    if len(written) == len(key) and key_form(written) != key_form(key):
        return key_text(key)
    return plain


def selector_of(part: ItemPart) -> str:
    """The selector text that names the item *part* names: ``"#n"`` for a position, as ``at(n)`` gives it, else ``key_selector`` of its key.

    This is the text only: whether it names that item and no other in a given
    list is for the caller to know (an item of the list may have the key ``#n``
    as text); ``item_selector`` checks it against a list.
    """
    if not isinstance(part, Key):
        return at(part)
    return key_selector(part.value)


def _anchor(selector: Selector) -> str | int | float | list[str | int | float]:
    """*selector* as JSON: the parts of a tuple as a list; a selector or part that is not a number or a string (a ``UUID``) in string form."""
    if not isinstance(selector, tuple):
        return _json_part(selector)
    return [_json_part(p) for p in selector]


def _json_part(value: object) -> str | int | float:
    """*value* as it is when a number (``bool`` included) or a string, else in string form."""
    return value if isinstance(value, (str, int, float)) else str(value)


def _place(
    first: bool,
    last: bool,
    before: Selector | None,
    after: Selector | None,
) -> str | dict[str, object] | None:
    """The ``_position`` value for the keywords given, or None for none."""
    given = [
        name
        for name, on in (
            ("first", first),
            ("last", last),
            ("before", before is not None),
            ("after", after is not None),
        )
        if on
    ]
    if len(given) > 1:
        raise TypeError(f"give one place, not {', '.join(given)}")
    if first:
        return "first"
    if last:
        return "last"
    if before is not None:
        return {"before": _anchor(before)}
    if after is not None:
        return {"after": _anchor(after)}
    return None


def add(
    body: Mapping[str, object] | None = None,
    *,
    first: bool = False,
    last: bool = False,
    before: Selector | None = None,
    after: Selector | None = None,
) -> dict[str, object]:
    """A new list item: ``body`` is its fields; the place is at most one keyword."""
    fields = dict(body or {})
    if "_op" in fields:
        raise TypeError("the body of add() must not hold '_op'")
    if "_position" in fields:
        raise TypeError(
            "the body of add() must not hold '_position'; "
            "give the place with first, last, before or after"
        )
    value: dict[str, object] = {"_op": "add", **fields}
    place = _place(first, last, before, after)
    if place is not None:
        value["_position"] = place
    return value


def delete() -> dict[str, object]:
    """Remove the item."""
    return {"_op": "delete"}


def move(
    *,
    first: bool = False,
    last: bool = False,
    before: Selector | None = None,
    after: Selector | None = None,
) -> dict[str, object]:
    """Move the item: exactly one of ``first``, ``last``, ``before``, ``after``."""
    place = _place(first, last, before, after)
    if place is None:
        raise TypeError("move() needs a place: first, last, before or after")
    return {"_position": place}


@dataclass(frozen=True, slots=True)
class Place:
    """Where an item goes. ``anchor`` is the selector for ``before`` and ``after``."""

    kind: Literal["first", "last", "before", "after"]
    anchor: object = None


@dataclass(frozen=True, slots=True)
class ItemOp:
    """One list item's value, parsed. ``body`` has no ``_op`` and no ``_position``."""

    kind: Literal["change", "add", "delete", "move"]
    body: dict[str, object]
    place: Place | None


def _selector(value: object, path: Path) -> Selector:
    """An anchor as written in a patch; a JSON array of key parts becomes a tuple."""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, list) and value:
        parts = cast("list[object]", value)
        if all(isinstance(p, (str, int, float)) for p in parts):
            return tuple(parts)
    raise PatchError(
        f'{value!r} is not a selector: write a key, a value, "#n" (at(n)) '
        "or a list of key parts",
        path,
        "selector",
    )


def _parse_place(value: object, path: Path) -> Place:
    if isinstance(value, str):
        if value in _POSITION_WORDS:
            return Place(value)
        for word in _POSITION_ANCHORS:
            if value.startswith(f"{word}:"):
                raise PatchError(
                    f'the "{word}:key" form of _position was removed; {_POSITION_FORMS}',
                    path,
                    "operation",
                )
    elif isinstance(value, dict):
        anchors = cast("dict[object, object]", value)
        if len(anchors) == 1:
            ((word, anchor),) = anchors.items()
            if word in _POSITION_ANCHORS:
                return Place(word, _selector(anchor, path))
        raise PatchError(
            f"_position takes exactly one of before and after; {_POSITION_FORMS}",
            path,
            "operation",
        )
    elif isinstance(value, int) and not isinstance(value, bool):
        raise PatchError(
            f"the integer form of _position was removed; {_POSITION_FORMS}",
            path,
            "operation",
        )
    raise PatchError(
        f"{value!r} is not a position; {_POSITION_FORMS}", path, "operation"
    )


def parse_item_op(value: object, path: Path) -> ItemOp:
    """Read the value under one selector of a list as an item operation.

    ``path`` is where the value sits in the patch, for the errors. An anchor
    given as a JSON array of key parts comes back as a tuple.
    """
    if value is None:
        raise PatchError(
            'null does not delete an item; write {"_op": "delete"} (delete())',
            path,
            "null",
        )
    if not isinstance(value, dict):
        raise PatchError(
            f"an item operation is an object, not {type(value).__name__}; write "
            + 'a plain object, {"_op": "add", ...} (add(...)) or {"_op": "delete"} '
            + "(delete())",
            path,
            "operation",
        )
    fields = dict(cast("dict[str, object]", value))
    has_op = "_op" in fields
    op = fields.pop("_op", None)
    has_position = "_position" in fields
    position = fields.pop("_position", None)
    if has_op and op not in ("add", "delete"):
        raise PatchError(
            f'_op is "add" or "delete", not {op!r}; write {{"_op": "add", ...}} '
            + '(add(...)) or {"_op": "delete"} (delete()), a change as a plain '
            + 'object, and a move as {"_position": ...} (move(...))',
            path,
            "operation",
        )
    if op == "delete":
        if fields or has_position:
            raise PatchError(
                '{"_op": "delete"} (delete()) takes no other key', path, "operation"
            )
        return ItemOp("delete", {}, None)
    place = _parse_place(position, path) if has_position else None
    if op == "add":
        return ItemOp("add", fields, place)
    if not fields:
        return ItemOp("move" if place is not None else "change", {}, place)
    return ItemOp("change", fields, place)


# Capped well below Python's limit on int digits: a longer "#n" is no position.
_POSITION_SELECTOR = re.compile(r"#[0-9]{1,18}", re.ASCII)


def position_of(selector: object) -> int | None:
    """The position ``"#n"`` names, or None when ``selector`` is not that form (``n`` of at most 18 digits)."""
    if isinstance(selector, str) and _POSITION_SELECTOR.fullmatch(selector):
        return int(selector[1:])
    return None


def is_new(selector: object) -> bool:
    """Whether ``selector`` is ``"+"`` or ``"+tag"``: a new item."""
    return isinstance(selector, str) and selector.startswith("+")


_ROOT = Path(())


def checked_patch(patch: object, path: Path = _ROOT) -> object:
    """A copy of *patch* made of new ``dict`` and ``list`` objects, or ``PatchError``.

    A patch holds what ``json.loads`` returns: dicts, lists, strings, finite
    numbers, booleans and ``None``, nested at most ``MAX_PATCH_DEPTH`` levels
    deep, the patch itself being the first. A selector may also be written
    the Python way: a dict key that is an ``int`` (not a ``bool``), a ``UUID``
    or a ``tuple`` of ``str``, ``int``, ``float`` and ``UUID`` parts. Keys are
    kept as they are, in their order. A value or key that is an instance of
    a subclass of ``int``, ``float`` or ``str`` (an ``IntEnum`` or ``StrEnum``
    member) is copied as the plain value. Anything else raises ``PatchError`` at
    the place it sits; a key that is not one raises at the place of its dict.
    The walk never goes deeper than the bound, so a patch of any depth, or one
    that holds itself, raises and does not exhaust the stack.

    *path* is where *patch* sits, for the errors and for the depth. The check
    runs before the patch is read against a model, so it cannot tell a
    selector from a field: a path made here names every object key as a name
    (``blobs[#0].a``).
    """
    return _check(patch, path.parts)


def _check(value: object, parts: tuple[PathPart, ...]) -> object:
    """*value* checked and copied. *parts* is where it sits (a ``Path`` is built only for an error)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return _plain(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return float(value)
        raise PatchError(
            f"{value!r} is not a JSON number: a number in a patch is finite",
            Path(parts),
            "not_json",
        )
    if isinstance(value, (dict, list)) and len(parts) >= MAX_PATCH_DEPTH:
        raise PatchError(
            f"the patch nests more than {MAX_PATCH_DEPTH} levels deep: split it "
            "into smaller patches, or address the place it changes with "
            "segment or path",
            Path(parts),
            "not_json",
        )
    if isinstance(value, list):
        items = cast("list[object]", value)
        return [_check(item, (*parts, index)) for index, item in enumerate(items)]
    if isinstance(value, dict):
        entries = cast("dict[object, object]", value)
        copied: dict[object, object] = {}
        for key, item in entries.items():
            if not _is_json_key(key):
                raise PatchError(
                    f"{key!r} is not a key of a JSON object: a key is a string, "
                    "or for a selector an int, a UUID or a tuple of key parts",
                    Path(parts),
                    "not_json",
                )
            plain_key = _plain(key)
            copied[plain_key] = _check(
                item,
                (*parts, plain_key if isinstance(plain_key, str) else str(plain_key)),
            )
        return copied
    if value is MISSING:
        raise PatchError(
            "MISSING is not a JSON value: write None (null) to unset a field",
            Path(parts),
            "not_json",
        )
    raise PatchError(_not_json_message(value), Path(parts), "not_json")


def _not_json_message(value: object) -> str:
    """What to say of *value*, which is not a JSON value: what it is, and what to give instead."""
    message = (
        f"{type(value).__name__} is not a JSON value: a patch holds dicts, "
        "lists, strings, numbers, booleans and None"
    )
    if isinstance(value, BaseModel):
        return message + '; give a model as its JSON-mode dump, model_dump(mode="json")'
    if isinstance(value, UUID):
        message += "; write a UUID as its text, str(value)"
    elif isinstance(value, Enum):
        message += "; give an Enum member as its value, member.value"
    elif isinstance(value, (datetime.date, datetime.time)):
        message += "; write a date or a time as its text, value.isoformat()"
    elif isinstance(value, (set, frozenset, tuple, Iterator)):
        message += "; give a set, a tuple or a generator as a list, list(value)"
    else:
        return message
    if isinstance(value, (UUID, Enum)):
        message += (
            '. A model dumped without mode="json" holds such values: '
            'dump it with model_dump(mode="json")'
        )
    return message


def _plain[T](value: T) -> T:
    """*value* as the plain ``int`` or ``str`` when it is an instance of a subclass of one (an ``IntEnum`` or ``StrEnum`` member), else itself."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and type(value) is not int:
        return cast("T", int(value))
    if isinstance(value, str) and type(value) is not str:
        return cast("T", str.__str__(value))
    return value


def _is_json_key(key: object) -> bool:
    """Whether *key* is a string or a selector written the Python way."""
    if isinstance(key, (str, UUID)):
        return True
    if isinstance(key, tuple):
        return all(
            isinstance(p, (str, int, float, UUID))
            for p in cast("tuple[object, ...]", key)
        )
    return isinstance(key, int) and not isinstance(key, bool)
