"""The patch format of ``VcoModel.merge_patch``.

``PatchError`` is what a patch that cannot be applied raises, with the place in
the patch it failed at. ``add``, ``delete``, ``move`` and ``at`` write list item
operations as plain JSON values; ``parse_item_op`` reads one back.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, override

from .rebase import Path


class PatchError(ValueError):
    """A patch that cannot be applied. ``path`` is where in the patch it failed.

    A pydantic ``ValidationError`` behind it is its ``__cause__``.
    """

    path: Path

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message, path)
        self.message: str = message
        self.path = path

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path.parts else self.message


type Selector = str | int | tuple[object, ...]
"""A key or value, ``at(n)``, or the tuple of the parts of a composite key."""

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


def _anchor(selector: Selector) -> str | int | list[str | int | float]:
    """*selector* as JSON: the parts of a tuple as a list, each part that is not a number or a string (a ``UUID``) in string form."""
    if not isinstance(selector, tuple):
        return selector
    return [p if isinstance(p, (str, int, float)) else str(p) for p in selector]


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
        f"{value!r} is not a selector: write a key, a value, at(n) "
        "or a list of key parts",
        path,
    )


def _parse_place(value: object, path: Path) -> Place:
    if isinstance(value, str):
        if value in _POSITION_WORDS:
            return Place(cast("Literal['first', 'last']", value))
        for word in _POSITION_ANCHORS:
            if value.startswith(f"{word}:"):
                raise PatchError(
                    f'the "{word}:key" form of _position was removed; {_POSITION_FORMS}',
                    path,
                )
    elif isinstance(value, dict):
        anchors = cast("dict[object, object]", value)
        if len(anchors) == 1:
            ((word, anchor),) = anchors.items()
            if word in _POSITION_ANCHORS:
                return Place(
                    cast("Literal['before', 'after']", word),
                    _selector(anchor, path),
                )
        raise PatchError(
            f"_position takes exactly one of before and after; {_POSITION_FORMS}",
            path,
        )
    elif isinstance(value, int) and not isinstance(value, bool):
        raise PatchError(
            f"the integer form of _position was removed; {_POSITION_FORMS}", path
        )
    raise PatchError(f"{value!r} is not a position; {_POSITION_FORMS}", path)


def parse_item_op(value: object, path: Path) -> ItemOp:
    """Read the value under one selector of a list as an item operation.

    ``path`` is where the value sits in the patch, for the errors. An anchor
    given as a JSON array of key parts comes back as a tuple.
    """
    if value is None:
        raise PatchError("null does not delete an item; write delete()", path)
    if not isinstance(value, dict):
        raise PatchError(
            f"an item operation is an object, not {type(value).__name__}", path
        )
    fields = dict(cast("dict[str, object]", value))
    has_op = "_op" in fields
    op = fields.pop("_op", None)
    has_position = "_position" in fields
    position = fields.pop("_position", None)
    if has_op and op not in ("add", "delete"):
        raise PatchError(
            f'_op is "add" or "delete", not {op!r}; write a change as a plain object',
            path,
        )
    if op == "delete":
        if fields or has_position:
            raise PatchError("delete() takes nothing else", path)
        return ItemOp("delete", {}, None)
    place = _parse_place(position, path) if has_position else None
    if op == "add":
        return ItemOp("add", fields, place)
    if not fields:
        if place is None:
            raise PatchError(
                "an empty object changes nothing; give fields, delete() or move()",
                path,
            )
        return ItemOp("move", {}, place)
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
