"""The patch that makes a list of changes.

``merge_patch`` turns a patch into changes; ``patch_of`` is the other way: it
takes the ``RawChange`` values of a plan, or of the edits that were lost, and
writes the plain JSON patch that makes them, places and moves included.
"""

import copy
import itertools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import (
    VcoModel,
    _field_accepts_missing,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_none,  # pyright: ignore[reportPrivateUsage]
    _field_model_types,  # pyright: ignore[reportPrivateUsage]
    _field_names_by_key,  # pyright: ignore[reportPrivateUsage]
    _merged_fields,  # pyright: ignore[reportPrivateUsage]
)
from .merge import (
    _compared_key,  # pyright: ignore[reportPrivateUsage]
    _list_spec,  # pyright: ignore[reportPrivateUsage]
    _ListSpec,  # pyright: ignore[reportPrivateUsage]
)
from .patch import position_of, selector_of
from .rebase import (
    ItemMove,
    ItemPart,
    ItemPlace,
    Path,
    RawChange,
    _json_item_key,  # pyright: ignore[reportPrivateUsage]
    key_form,
)

type _Node = dict[str, object]
"""An object of the patch."""


@dataclass(frozen=True, slots=True)
class _Plan:
    """What one change writes, worked out against the model (pass 1).

    ``steps`` are the keys of the objects from the root of the patch to the
    object that takes the entry: field names, item selectors and map keys.
    """

    change: RawChange
    kind: Literal["value", "add", "delete", "set", "order", "map_add", "map_delete"]
    steps: tuple[str, ...]
    key: str = ""
    """The field name of a value, the selector of a delete or a set.

    For a map entry it is the key of the entry.
    """
    value: object = None
    """The value of a field, or the fields of an item that is added or set."""
    list_path: Path | None = None
    """Where the keyed list of an item or of an order entry is.

    The item is an added item or an item that is set.
    """
    item: ItemPart | None = None
    """How the changes name the item an add adds or a set sets."""
    moves: tuple[tuple[ItemMove, str], ...] = ()
    """The items an order entry moves, each with its selector."""

    def listed(self) -> Path:
        """``list_path``, which a plan of an add, a set or an order has."""
        if self.list_path is None:
            raise AssertionError(f"a plan of kind {self.kind} has no list_path")
        return self.list_path

    def listed_item(self) -> tuple[Path, ItemPart]:
        """``list_path`` and ``item``, which a plan of an add or a set has."""
        if self.item is None:
            raise AssertionError(f"a plan of kind {self.kind} has no item")
        return self.listed(), self.item


@dataclass(frozen=True, slots=True)
class _Placed:
    """An item of a keyed list that gets a ``_position``.

    It comes with the plan that places it.
    """

    plan: _Plan
    item: ItemPart
    place: ItemPlace


def patch_of(
    changes: Iterable[RawChange],
    model: type[VcoModel],
    *,
    stored_parts: Mapping[Path, ItemPart] | None = None,
) -> tuple[dict[str, object], tuple[RawChange, ...]]:
    """The patch that makes *changes* to a *model*, and the changes it leaves out.

    The patch is plain JSON: ``json.loads(json.dumps(patch)) == patch``, and
    ``merge_patch`` takes it. A field is written under its python name, a value
    as the change has it. An item added to a keyed list is an add
    (``{"_op": "add", ...}``, ``add(...)``) with its place, a removed one a
    delete (``{"_op": "delete"}``, ``delete()``), an item that moves a place
    (``{"_position": "first"}``, ``"last"``, ``{"before": ...}`` or
    ``{"after": ...}``; ``move(first=True)`` and the like). A run of items
    placed one after the other is written backwards, each after the item the
    run follows, because a place may only name an item that is in the list and
    stays where it is.

    A change no patch can make is left out and writes nothing: a key the model
    has no field for, a field that cannot be unset, a value below one that a
    patch replaces whole, an item of a list without keys, a list value that
    repeats a key, a map entry whose key reads as a new item or a position,
    the document as a whole. So is a change that needs an item of the stored
    list that *stored_parts* has no entry for, or a place after one.

    *stored_parts* maps the path of an item (``rules[a]``, in the names the
    changes use) to the part that names it in the list as stored: its key
    there, or its position. Without it, an item is named as the changes name it.
    """
    given = list(changes)
    resolved = [(change, _resolve(change, model, stored_parts)) for change in given]
    plans = _placeable([plan for _, plan in resolved if plan is not None], stored_parts)
    written = {id(plan) for plan in plans}
    left_out = tuple(
        change for change, plan in resolved if plan is None or id(plan) not in written
    )
    return _write(plans, stored_parts), left_out


# ---------------------------------------------------------------------------
# Pass 1: work each change out
# ---------------------------------------------------------------------------


def _stored(
    stored_parts: Mapping[Path, ItemPart] | None, list_path: Path, part: ItemPart
) -> ItemPart | None:
    """The part that names the item *part* names in the stored list.

    None if it has none.
    """
    if stored_parts is None:
        return part
    return stored_parts.get(list_path.child(part))


def _selector(
    stored_parts: Mapping[Path, ItemPart] | None, list_path: Path, part: ItemPart
) -> str | None:
    """The selector of the item *part* names; None if the stored list has none."""
    stored = _stored(stored_parts, list_path, part)
    return None if stored is None else selector_of(stored)


def _resolve(  # noqa: C901, PLR0911, PLR0912 (existing: split it when it next changes)
    change: RawChange,
    model: type[VcoModel],
    stored_parts: Mapping[Path, ItemPart] | None,
) -> _Plan | None:
    """What *change* writes, or None when no patch can make it."""
    parts = change.path.parts
    fields: Mapping[str, FieldInfo] = model.model_fields
    steps: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if not isinstance(part, str):
            return None
        name = _field_names_by_key(fields).get(part)
        if name is None:
            return None
        field_info = fields[name]
        index += 1
        ends = index == len(parts)
        spec = _list_spec(field_info)
        if ends:
            if spec is not None and spec.kind == "keyed" and change.is_order:
                return _order_plan(change, (*steps, name), change.path, stored_parts)
            return _value_plan(change, tuple(steps), name, field_info, spec)
        if spec is not None and spec.kind == "keyed":
            item = parts[index]
            if isinstance(item, str):
                return None
            index += 1
            list_path = Path(parts[: index - 1])
            steps.append(name)
            if index == len(parts):
                return _item_plan(
                    change,
                    tuple(steps),
                    list_path,
                    item,
                    stored_parts,
                    None if spec.item_type is None else spec.item_type.fields,
                )
            selector = _selector(stored_parts, list_path, item)
            if selector is None or spec.item_type is None:
                return None
            steps.append(selector)
            fields = spec.item_type.fields
        elif spec is not None and spec.kind == "map":
            key = parts[index]
            if not isinstance(key, str):
                return None
            index += 1
            steps.append(name)
            if index == len(parts):
                return _map_plan(change, tuple(steps), key)
            if spec.map_type is None:
                return None
            steps.append(key)
            fields = spec.map_type.model_fields
        else:
            types = _field_model_types(field_info)
            if not types:
                return None
            steps.append(name)
            fields = _merged_fields(types)
    return None


def _order_plan(
    change: RawChange,
    steps: tuple[str, ...],
    list_path: Path,
    stored_parts: Mapping[Path, ItemPart] | None,
) -> _Plan | None:
    """The plan of the order entry of a keyed list.

    It is its moves, each by the selector of its item.
    """
    moves: list[tuple[ItemMove, str]] = []
    for move in change.moves or ():
        selector = _selector(stored_parts, list_path, move.item)
        if selector is None:
            return None
        moves.append((move, selector))
    return _Plan(change, "order", steps, list_path=list_path, moves=tuple(moves))


def _object(value: object) -> _Node | None:
    """A copy of *value* when it is an object, else None."""
    if not isinstance(value, dict):
        return None
    return copy.deepcopy(cast("_Node", value))


def _nulls_writable(body: _Node, fields: Mapping[str, FieldInfo]) -> bool:
    """Whether every ``None`` in *body* is written by a ``null``.

    *body* is the fields of an item that exists.

    For an item that exists, a patch's ``null`` unsets a field that takes
    ``MISSING``, so it writes ``None`` only where the field takes that and not
    ``MISSING``; a field that takes neither is refused.
    """
    names = _field_names_by_key(fields)
    for key, value in body.items():
        name = names.get(key)
        if value is None and name is not None:
            field_info = fields[name]
            if not _field_accepts_none(field_info) or _field_accepts_missing(
                field_info
            ):
                return False
    return True


def _item_plan(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    change: RawChange,
    steps: tuple[str, ...],
    list_path: Path,
    item: ItemPart,
    stored_parts: Mapping[Path, ItemPart] | None,
    fields: Mapping[str, FieldInfo] | None,
) -> _Plan | None:
    """The plan of a change that ends at an item of a keyed list.

    It adds, deletes, or sets the item's fields.

    *fields* are the fields of the items of the list. A set is left out whole
    when it gives a field a ``None`` that a patch's ``null`` cannot write.
    """
    after = _object(change.after)
    if change.before is MISSING and after is not None:
        return _Plan(change, "add", steps, value=after, list_path=list_path, item=item)
    selector = _selector(stored_parts, list_path, item)
    if selector is None:
        return None
    if change.after is MISSING:
        return _Plan(change, "delete", steps, key=selector)
    if isinstance(change.before, dict) and after:
        if fields is not None and not _nulls_writable(after, fields):
            return None
        return _Plan(
            change,
            "set",
            steps,
            key=selector,
            value=after,
            list_path=list_path,
            item=item,
        )
    return None


def _map_plan(change: RawChange, steps: tuple[str, ...], key: str) -> _Plan | None:
    """The plan of a change that ends at an entry of a map of models.

    It adds or deletes the entry.
    """
    after = _object(change.after)
    if change.before is MISSING and after is not None:
        if key.startswith("+") or position_of(key) is not None:
            return None
        return _Plan(change, "map_add", steps, key=key, value=after)
    if change.after is MISSING:
        return _Plan(change, "map_delete", steps, key=key)
    return None


def _value_plan(
    change: RawChange,
    steps: tuple[str, ...],
    name: str,
    field_info: FieldInfo,
    spec: _ListSpec | None,
) -> _Plan | None:
    """The plan of a change that ends at a field: the field's new value."""
    if change.is_order:
        return None
    after = change.after
    if spec is not None and spec.kind == "map" and isinstance(after, dict):
        return _new_map_plan(change, steps, name, cast("_Node", after))
    if after is MISSING:
        if not _field_accepts_missing(field_info):
            return None
        value: object = None
    elif after is None:
        # A patch's null unsets a field that takes MISSING.
        if not _field_accepts_none(field_info) or _field_accepts_missing(field_info):
            return None
        value = None
    elif (
        spec is not None
        and spec.kind == "keyed"
        and isinstance(after, list)
        and _repeats_a_key(cast("list[object]", after), spec)
    ):
        return None
    else:
        value = copy.deepcopy(cast("object", after))
    return _Plan(change, "value", steps, key=name, value=value)


def _new_map_plan(
    change: RawChange, steps: tuple[str, ...], name: str, entries: _Node
) -> _Plan | None:
    """The plan of a map of models that was not there and now has *entries*.

    It is one add each.

    A patch's object on a map is item operations, so the entries are written
    as adds. Left out when the map was there, or when an entry is not an object
    or has a key that an add cannot be written under.
    """
    if change.before is not MISSING:
        return None
    adds: _Node = {}
    for key, entry in entries.items():
        body = _object(entry)
        if body is None or key.startswith("+") or position_of(key) is not None:
            return None
        adds[key] = {"_op": "add", **body}
    return _Plan(change, "value", steps, key=name, value=adds)


def _repeats_a_key(items: list[object], spec: _ListSpec) -> bool:
    """Whether two *items* of a keyed list value have one key.

    ``merge_patch`` refuses that. Keys are compared as ``merge_patch``
    compares them (``_compared_key``).
    """
    if spec.item_type is None:
        return False
    variant = spec.item_type.variants[0]
    forms = [
        _compared_key(spec, key_form(_json_item_key(item, variant, spec.key_paths)))
        for item in items
    ]
    present = [form for form in forms if form is not None]
    return len(set(present)) != len(present)


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


def _placed_by_list(plans: Iterable[_Plan]) -> dict[Path, dict[ItemPart, _Placed]]:
    """The items each keyed list places, by the name the changes give them."""
    out: dict[Path, dict[ItemPart, _Placed]] = {}
    for plan in plans:
        if plan.list_path is None:
            continue
        placed = out.setdefault(plan.list_path, {})
        if plan.kind == "order":
            for move, _ in plan.moves:
                placed[move.item] = _Placed(plan, move.item, move.place)
        elif plan.item is not None and plan.change.place is not None:
            placed[plan.item] = _Placed(plan, plan.item, plan.change.place)
    return out


def _anchor(
    placed: Mapping[ItemPart, _Placed], item: ItemPart
) -> tuple[int, ItemPart | None]:
    """How many placed items are between *item* and the item its run follows.

    The item it follows is returned too (None: first).
    """
    depth, after = 0, placed[item].place.after
    while after is not None and after in placed and depth < len(placed):
        depth, after = depth + 1, placed[after].place.after
    return depth, after


def _placeable(
    plans: list[_Plan], stored_parts: Mapping[Path, ItemPart] | None
) -> list[_Plan]:
    """*plans* without those that place an item after one the stored list does not have.

    An added item is then not added, and an order entry is dropped whole.
    Dropping one plan may leave another without the item it follows, so this
    repeats until none is left.
    """
    while True:
        dropped = {
            id(placed[item].plan)
            for list_path, placed in _placed_by_list(plans).items()
            for item in placed
            if not _follows_an_item_there(placed, item, list_path, stored_parts)
        }
        if not dropped:
            return plans
        plans = [plan for plan in plans if id(plan) not in dropped]


def _follows_an_item_there(
    placed: Mapping[ItemPart, _Placed],
    item: ItemPart,
    list_path: Path,
    stored_parts: Mapping[Path, ItemPart] | None,
) -> bool:
    """Whether the item the run of *item* follows can be named.

    It can when it is first, or when the stored list has the item.
    """
    _, after = _anchor(placed, item)
    if after is None:
        return True
    return after not in placed and _stored(stored_parts, list_path, after) is not None


# ---------------------------------------------------------------------------
# Pass 2: write
# ---------------------------------------------------------------------------


def _node(patch: _Node, steps: tuple[str, ...]) -> _Node:
    """The object of *patch* at *steps*, made where it is not there."""
    node = patch
    for step in steps:
        child = node.get(step)
        if not isinstance(child, dict):
            child = node[step] = {}
        node = cast("_Node", child)
    return node


def _new_selectors() -> Iterator[str]:
    """``"+"``, ``"+2"``, ``"+3"`` and so on: the selectors of new items."""
    yield "+"
    yield from (f"+{n}" for n in itertools.count(2))


def _added_selector(item: ItemPart, node: _Node) -> str:
    """The selector an add of *item* goes under in *node*, the object of its list.

    The item's own selector, unless that names a position or is taken: then a
    new item's, ``"+"`` or ``"+tag"``, which the body of the add names.
    """
    selector = selector_of(item)
    if selector in node or position_of(selector) is not None:
        return next(s for s in _new_selectors() if s not in node)
    return selector


def _write(plans: list[_Plan], stored_parts: Mapping[Path, ItemPart] | None) -> _Node:  # noqa: C901, PLR0912 (existing: split it when it next changes)
    """The patch the *plans* write, in the order given, then the places."""
    patch: _Node = {}
    entries: dict[tuple[Path, ItemPart], tuple[_Node, str]] = {}
    for plan in plans:
        if plan.kind == "order" and not plan.moves:
            continue
        node = _node(patch, plan.steps)
        if plan.kind == "value":
            node[plan.key] = plan.value
        elif plan.kind == "map_add":
            node[plan.key] = {"_op": "add", **cast("_Node", plan.value)}
        elif plan.kind in ("delete", "map_delete"):
            node[plan.key] = {"_op": "delete"}
        elif plan.kind == "add":
            list_path, item = plan.listed_item()
            selector = _added_selector(item, node)
            node[selector] = {"_op": "add", **cast("_Node", plan.value)}
            entries[list_path, item] = (node, selector)
        elif plan.kind == "set":
            list_path, item = plan.listed_item()
            entry = node.get(plan.key)
            if not isinstance(entry, dict):
                entry = node[plan.key] = {}
            cast("_Node", entry).update(cast("_Node", plan.value))
            entries[list_path, item] = (node, plan.key)
        else:
            list_path = plan.listed()
            for move, selector in plan.moves:
                entries[list_path, move.item] = (node, selector)
    for list_path, placed in _placed_by_list(plans).items():
        # The deepest item of a run first: each is written after the item the run
        # follows, so that the run is in order when the patch is applied.
        for item in sorted(placed, key=lambda it: -_anchor(placed, it)[0]):
            _, after = _anchor(placed, item)
            node, selector = entries[list_path, item]
            entry = cast("_Node", node.pop(selector, {}))
            if after is None:
                entry["_position"] = "first"
            else:
                anchor = _selector(stored_parts, list_path, after)
                if anchor is None:
                    raise AssertionError("_placeable kept a place after no item")
                entry["_position"] = {"after": anchor}
            node[selector] = entry
    return patch
