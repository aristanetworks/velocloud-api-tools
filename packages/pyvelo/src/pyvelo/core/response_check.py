"""The response check: compare what the VCO stored with what a write meant to change.

Three JSON documents are walked together: the data as read ``R``, the payload
``P`` that was sent, and the data ``D`` the VCO stored. A difference between
``R`` and ``P`` is a change the plan made; ``D`` says whether the VCO applied
it. A difference between ``P`` and ``D`` anywhere else is a change the VCO
made on its own.

At each place the first of these rules that fits is used. ``MISSING`` stands
for a side that has no value there.

1. ``R`` equals ``P`` (``same_json``): nothing is planned here. Every
   difference between ``P`` and ``D`` (``raw_changes``) is a server change.
2. All three are objects: each key is walked, those of ``R`` first, then those
   only ``P`` has, then those only ``D`` has.
3. A keyed list whose items ``pair_items`` pairs between ``R`` and ``P``, and
   ``D`` is a list: the items of ``P`` are paired with those of ``D``
   (``_pair_stored``), and each is walked with its partners. An item the plan
   removed is one planned change, and an item only ``D`` has is one server
   change. An added item is one planned change with its place. The order is
   one more planned change, at the list, when the plan reordered the items or
   added one that ``D`` has and ``D`` holds the items of ``P`` in another
   order than ``P``; it is applied when ``D`` holds them in the order of
   ``P``. An order the VCO changed where the plan placed nothing is a server
   change.
4. ``R`` and ``P`` are objects, or such a keyed list, and ``D`` is not: every
   difference between ``R`` and ``P`` is a planned change with nothing stored
   at its path, so a removal is applied and any other change is not. What
   ``D`` has in place of the container is one server change.
5. Anything else is one planned change: a scalar that was set, an object that
   was set or added, a list replaced whole (a list without a key, or a keyed
   list ``pair_items`` cannot pair), a key or an item that was removed. It is
   applied when ``D`` holds the payload's value (``_beyond``): ``D`` may hold
   more keys than the payload, but not a key ``R`` had and the payload lacks.
   Two values are equal, here and in rule 1, when ``same_json`` says so: a
   boolean is never a number, so a sent ``true`` stored as ``1`` is not
   applied, and ``1`` is ``1.0``.

A path names an item of a keyed list as ``raw_changes`` does: by its key, or by its
position ``#n`` where the key repeats or there is none. An item that was read is named
as read, also when it was renamed or moved; an added item by its key or position in the
payload; an item only the VCO has by its key or position in the stored data. So two
entries share a path only where a key repeats or an item has none; their values tell
them apart. A keyed list in which an added item would have the name of an item as read
is compared whole (rule 5).

The VCO stores some values otherwise than as sent; ``Rewrites`` says where.
A key it owns (a field marked ``ServerOwned``, or a path a pattern matches)
is not compared: a change the plan made there is left out (``left_out``),
and a value stored there otherwise than sent is a server change. So is the
removal of a default the VCO adds back. With IPv6 canonical form on, two
strings that parse as one IPv6 interface are the same value, and so are two
such parts of a list item's key: the item pairs, and the key text stored
otherwise is a server change. A keyed list whose order the VCO sets itself
(``own_order``) has no planned order: a reorder is left out, and an order
stored otherwise than sent is a server change.

The optional *model* is the ``VcoModel`` class the documents are data of. It
supplies the ``KeyField`` of each list, as it does for ``rebase``, and the
fields marked ``ServerOwned``. With ``model=None`` every list is unkeyed, so
a changed list is one change.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import IPv6Interface
from typing import Final, Literal, cast

from pydantic import MISSING

from .base import (
    VcoModel,
    is_dict,
    is_list,
    list_item_type,
    same_json,
)
from .fields import is_server_owned
from .rebase import (
    ItemMove,
    ItemPart,
    ItemPlace,
    KeyedList,
    KeyForm,
    Node,
    Pairing,
    Path,
    PathPart,
    RawChange,
    collect_changes,
    item_names,
    item_parts,
    item_text,
    key_form,
    key_forms,
    node_child,
    node_field,
    pair_items,
    pair_unique,
    place_of,
    python_path,
    read_path,
    staying,
)

type PathPattern = tuple[str, ...]
"""The names of a path, in order.

``*`` is any one part (a name, a keyed item, a position); ``**`` is any number of parts,
none included.
"""


@dataclass(frozen=True, slots=True)
class Rewrites:
    """How the VCO stores some values of one module otherwise than as sent."""

    server_owned: tuple[PathPattern, ...] = ()
    """Keys whose value the VCO owns, as a ``ServerOwned`` field is."""
    readded: tuple[PathPattern, ...] = ()
    """Keys the VCO adds a default back at when they are removed."""
    ipv6: bool = False
    """Two strings that parse as one ``IPv6Interface`` are the same value."""
    own_order: tuple[PathPattern, ...] = ()
    """Keyed lists whose order the VCO sets itself."""


_NO_REWRITES: Final = Rewrites()
"""The default of ``check_response``: the VCO stores every value as sent."""


def matches(path: Path, pattern: PathPattern) -> bool:
    """Whether *path* is one of the paths *pattern* describes.

    A name of the pattern matches a name of the path with the same text. A
    keyed item or a position matches only ``*`` and ``**``.
    """
    return _matches(path.parts, pattern)


def _matches(parts: tuple[PathPart, ...], pattern: PathPattern) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_matches(parts[n:], rest) for n in range(len(parts) + 1))
    if not parts:
        return False
    part = parts[0]
    if head != "*" and not (isinstance(part, str) and part == head):
        return False
    return _matches(parts[1:], rest)


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One change the plan made, and what the VCO holds there now."""

    path: Path
    as_read: object  # MISSING: nothing was there
    intended: object  # MISSING: a removal
    stored: object  # MISSING: nothing is there
    part: Literal["data", "refs"] = "data"
    place: ItemPlace | None = None
    """Where an added item of a keyed list was planned to go."""
    moves: tuple[ItemMove, ...] | None = None
    """Not None for the order entry of a keyed list.

    It is the moves that put the stored order right.
    """
    owner: Path | None = None
    """For a ``refs`` entry of a ``RefOnly`` owner: the owner's path in python names."""

    @property
    def is_order(self) -> bool:
        """Whether this is the order entry of a keyed list."""
        return self.moves is not None

    def python_path(self, model: type[VcoModel] | None) -> Path:
        """The path in the names a patch of *model* is written in.

        *model* is the module's data class. The path of a ``refs`` entry is
        returned as it is: a row of refs is not a field of the data.
        """
        return self.path if self.part == "refs" else python_path(self.path, model)

    @property
    def clash(self) -> bool:
        """The stored value is not the one read: something else changed it too.

        For an order entry: the items that were read are stored in another
        order than read (an item only one of the two lists has does not count).
        True when either value is not a list. Never for a ``refs`` entry: a
        row's content is not compared, so a row there cannot differ from itself.
        """
        if self.part == "refs":
            return False
        if not self.is_order:
            return not same_json(self.stored, self.as_read)
        if not isinstance(self.as_read, list) or not isinstance(self.stored, list):
            return True
        read, stored = set(self.as_read), set(self.stored)
        return [name for name in self.as_read if name in stored] != [
            name for name in self.stored if name in read
        ]


@dataclass(frozen=True, slots=True)
class ServerChange:
    """What the VCO stored otherwise than sent, at a path that is not a lost edit.

    Where the plan changed nothing, and also where the VCO stores a planned
    value its own way (a left-out change, an IPv6 address in canonical form).
    """

    path: Path
    as_read: object
    stored: object
    part: Literal["data", "refs"] = "data"
    merged: bool = False
    """The change was found before the write and merged into it: the plan was
    rebased onto the module as it was then, so the write sent it as it was."""

    def python_path(self, model: type[VcoModel] | None) -> Path:
        """The path in the names a patch of *model* is written in.

        *model* is the module's data class. The path of a ``refs`` entry is
        returned as it is: a row of refs is not a field of the data.
        """
        return self.path if self.part == "refs" else python_path(self.path, model)


@dataclass(frozen=True, slots=True)
class ResponseCheck:
    """What ``check_response`` found."""

    applied: tuple[PlannedChange, ...] = ()
    """The planned changes the VCO stored."""
    not_applied: tuple[PlannedChange, ...] = ()
    """The planned changes it did not: the old value is there, or another one."""
    left_out: tuple[PlannedChange, ...] = ()
    """The planned changes that count as neither: a change to a value the VCO
    owns, and the removal of a default it adds back (``Rewrites``)."""
    server_changes: tuple[ServerChange, ...] = ()
    """What else differs between the payload and what is stored.

    Each entry's ``as_read`` differs from its ``stored``."""
    stored_parts: Mapping[Path, ItemPart] = field(default_factory=dict[Path, ItemPart])
    """For each item of every keyed list the check walks, that the payload has and
    the stored list holds too, by the item's path (named as the plan names it),
    the part that names it in the stored list: its key there, or its position.
    An item that no selector can name alone has no entry: its position ``n`` is
    also, as text, the key ``#n`` of another item of the stored list, and a
    selector is read as a key before a position."""


def check_response(
    as_read: object,
    payload: object,
    stored: object,
    model: type[VcoModel] | None = None,
    *,
    rewrites: Rewrites = _NO_REWRITES,
) -> ResponseCheck:
    """Compare what the VCO stored with what a write meant to change.

    *as_read* is the data the payload was made from, *payload* the data that
    was sent, and *stored* the data the VCO returned for the write (or a read
    made after it). All three are JSON, of *model* when one is given.

    Every difference between *as_read* and *payload* is a planned change, at
    the deepest path that can be told apart (the items of a keyed list by
    their key, as ``raw_changes`` pairs them). It is applied when *stored*
    holds the payload's value there: the same scalar; for an object, every
    leaf of the payload's (*stored* may hold more keys, but not a key
    *as_read* had and *payload* lacks); for a list replaced whole, the
    payload's items in the payload's order; nothing, for a removal.
    An item is named as ``raw_changes`` names it: as read when it was read,
    by its key or position in the payload when it was added. An added item's
    change has its ``place``. The order of a keyed list is one more change at
    the list (``is_order``) when the plan reordered the items, or added an
    item that *stored* has and *stored* holds the payload's items in another
    order than the payload. Its three values are the items' names in *as_read*,
    in *payload* and in *stored*, and its ``moves`` are the moves from the
    stored order to the planned one (none when it is applied). An edit inside
    an item is another change. Whatever else differs between *payload* and
    *stored*, an order the VCO changed where the plan placed nothing included,
    is a server change.

    *rewrites* are what the VCO stores otherwise than as sent. A planned
    change to a key it owns, or the removal of a default it adds back, is
    neither applied nor not applied but left out, and what is stored there is
    a server change. For a list in ``rewrites.own_order`` the order is not
    held to the plan: a reorder is left out, with its ``moves``, and an order
    stored otherwise than sent is a server change.
    """
    found = _Found(rewrites)
    _walk(found, _Here(Path(()), as_read, payload, stored, model))
    return ResponseCheck(
        applied=tuple(found.applied),
        not_applied=tuple(found.not_applied),
        left_out=tuple(found.left_out),
        # A server change is a difference: one that stores the value as read
        # shows none (the planned change at its path says what was sent).
        server_changes=tuple(
            change
            for change in found.server_changes
            if not same_json(change.as_read, change.stored)
        ),
        stored_parts=found.stored_parts,
    )


@dataclass(slots=True)
class _Found:
    """The rewrites the walk allows for, and the changes it has found so far."""

    rewrites: Rewrites
    applied: list[PlannedChange] = field(default_factory=list)
    not_applied: list[PlannedChange] = field(default_factory=list)
    left_out: list[PlannedChange] = field(default_factory=list)
    server_changes: list[ServerChange] = field(default_factory=list)
    stored_parts: dict[Path, ItemPart] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ModelList:
    """A list of models without a key (undeclared or ``Positional``).

    ``rebase`` knows nothing of its items, so a change inside it is a change
    of the list. The check still reads the ``ServerOwned`` fields of its items.
    """

    item: type[VcoModel]


type _CheckNode = Node | _ModelList
"""What a JSON value is known to be (as ``Node`` says), or a keyless list of models."""


def _rebase_node(node: _CheckNode) -> Node:
    """*node* as ``rebase`` knows it: a list of models without a key is untyped."""
    return None if isinstance(node, _ModelList) else node


def _child_node(node: _CheckNode, key: object) -> _CheckNode:
    """What the value under *key* is known to be.

    As ``node_child`` says, and a ``_ModelList`` for a list of models without a
    ``KeyField`` (of the first variant, for a discriminated union). For a keyed
    list ``node_child`` gives a ``KeyedList``, whose ``.item`` is the first
    variant.
    """
    parent = _rebase_node(node)
    child = node_child(parent, key)
    if child is None and (found := node_field(parent, key)) is not None:
        item_type = list_item_type(found[1])
        if item_type is not None:
            return _ModelList(item_type.variants[0])
    return child


def _item_node(node: _CheckNode) -> _CheckNode:
    """What each item of a list of *node* is known to be.

    For a list of several kinds of item, the first variant: the response check
    reads every item of such a list as that kind, where the rebase reads each
    item as the kind it is.
    """
    return node.item if isinstance(node, (KeyedList, _ModelList)) else None


def _is_owned(rewrites: Rewrites, node: _CheckNode, key: object, path: Path) -> bool:
    """Whether the VCO owns the value under *key*, at *path*.

    It does when the field *key* is read as is marked ``ServerOwned``, or
    *path* matches a pattern of ``rewrites.server_owned``.
    """
    found = node_field(_rebase_node(node), key)
    return (found is not None and is_server_owned(found[1])) or any(
        matches(path, pattern) for pattern in rewrites.server_owned
    )


def _is_owned_below(
    rewrites: Rewrites, path: Path, node: _CheckNode, below: Path
) -> bool:
    """Whether *below*, a path under *path*, is a key the VCO owns.

    The node of *path* is *node*.
    """
    parts = below.parts[len(path.parts) :]
    if not parts or not isinstance(parts[-1], str):
        return False
    parent = node
    for part in parts[:-1]:
        parent = (
            _child_node(parent, part) if isinstance(part, str) else _item_node(parent)
        )
    return _is_owned(rewrites, parent, parts[-1], below)


def is_owned_path(
    path: Path, model: type[VcoModel] | None, rewrites: Rewrites = _NO_REWRITES
) -> bool:
    """Whether *path*, from the top of data of *model*, ends at a key the VCO owns.

    It is judged as the check judges a key: a field marked ``ServerOwned``, or
    a path a pattern of ``rewrites.server_owned`` matches. A path whose last
    part is an item of a list, not a key, is not owned.
    """
    return _is_owned_below(rewrites, Path(()), model, path)


def _differences(
    path: Path, old: object, new: object, node: _CheckNode
) -> list[RawChange]:
    """What ``raw_changes`` lists between *old* and *new*, with *path* before each path.

    ``raw_changes`` starts at a model; this starts at any node, a keyed list
    included.
    """
    out: list[RawChange] = []
    collect_changes(out, path, old, new, _rebase_node(node))
    return out


@dataclass(frozen=True, slots=True)
class _Here:
    """One place of the walk: its path, the three values there and the model's shape."""

    path: Path
    r: object
    p: object
    d: object
    node: _CheckNode

    def at(
        self, part: PathPart, r: object, p: object, d: object, node: _CheckNode
    ) -> "_Here":
        """The place one *part* below this one, with its three values and its node."""
        return _Here(self.path.child(part), r, p, d, node)


def _walk(found: _Found, here: _Here) -> None:
    if same_json(here.r, here.p):
        # Rule 1: nothing planned here.
        found.server_changes.extend(
            ServerChange(change.path, change.before, change.after)
            for change in _differences(here.path, here.p, here.d, here.node)
        )
    elif is_dict(here.r) and is_dict(here.p):
        if is_dict(here.d):
            _walk_object(found, here)
        else:
            _walk_replaced(found, here)
    elif (
        isinstance(here.node, KeyedList)
        and is_list(here.r)
        and is_list(here.p)
        and (pairing := pair_items(here.r, here.p, here.node)) is not None
        and (names := item_names(here.r, here.p, here.node, pairing)) is not None
    ):
        if is_list(here.d):
            _walk_keyed_list(found, here, pairing, names)
        else:
            _walk_replaced(found, here)
    else:
        _planned(found, here)


def _walk_object(found: _Found, here: _Here) -> None:
    """Rule 2: walk each key of *r*, then those only *p* has, then those only *d* has.

    A key the VCO owns is not walked (``_owned``).
    """
    r = cast("dict[object, object]", here.r)
    p = cast("dict[object, object]", here.p)
    d = cast("dict[object, object]", here.d)
    keys = [
        *r,
        *(key for key in p if key not in r),
        *(key for key in d if key not in r and key not in p),
    ]
    for key in keys:
        key_path = here.path.child(str(key))
        values = r.get(key, MISSING), p.get(key, MISSING), d.get(key, MISSING)
        if _is_owned(found.rewrites, here.node, key, key_path):
            _owned(found, key_path, *values)
        else:
            _walk(found, here.at(str(key), *values, _child_node(here.node, key)))


def _owned(found: _Found, path: Path, r: object, p: object, d: object) -> None:
    """A key the VCO owns, which is not walked.

    A change the plan made there is left out, and a value stored there
    otherwise than sent is a server change.
    """
    if not same_json(r, p):
        found.left_out.append(PlannedChange(path, r, p, d))
    if not same_json(p, d):
        found.server_changes.append(ServerChange(path, r, d))


def _walk_replaced(found: _Found, here: _Here) -> None:
    """Rule 4: *d* is not the container *r* and *p* are, so it stores nothing inside it.

    Each difference between *r* and *p* is a planned change that meets nothing
    stored. By rule 5's test that applies a removal, and nothing else. One at
    a key the VCO owns is left out.
    """
    for change in _differences(here.path, here.r, here.p, here.node):
        if _is_owned_below(found.rewrites, here.path, here.node, change.path):
            found.left_out.append(
                PlannedChange(
                    change.path,
                    change.before,
                    change.after,
                    MISSING,
                    place=change.place,
                    moves=change.moves,
                )
            )
        else:
            _planned(
                found,
                _Here(change.path, change.before, change.after, MISSING, None),
                place=change.place,
                moves=change.moves,
            )
    found.server_changes.append(ServerChange(here.path, here.r, here.d))


def _walk_keyed_list(
    found: _Found, here: _Here, pairing: Pairing, names: dict[int, ItemPart]
) -> None:
    """Rule 3: the items of a keyed list, each with its partners, and then the order.

    *names* is the name of each item of *p* (``item_names``).
    """
    r = cast("list[object]", here.r)
    p = cast("list[object]", here.p)
    d = cast("list[object]", here.d)
    node = cast("KeyedList", here.node)
    r_of = {in_p: in_r for in_r, in_p in pairing.pairs}
    d_of, d_of_removed = _pair_stored(here, pairing.removed, found.rewrites)
    r_parts, d_parts = (item_parts(items, node) for items in (r, d))
    d_keys = set(key_forms(d, node))

    def record(item_path: Path, k: int) -> None:
        """Note the part naming item *k* of *d*, when a selector can name it alone."""
        part = d_parts[k]
        if isinstance(part, int) and f"#{part}" in d_keys:
            return
        found.stored_parts[item_path] = part

    for j, item in enumerate(p):
        stored = d[d_of[j]] if j in d_of else MISSING
        if j in d_of:
            record(here.path.child(names[j]), d_of[j])
        if j in r_of:
            _walk(found, here.at(names[j], r[r_of[j]], item, stored, node.item))
        else:
            _planned(
                found,
                here.at(names[j], MISSING, item, stored, node.item),
                place=place_of(names, j),
            )
    for i in pairing.removed:
        still_there = d[d_of_removed[i]] if i in d_of_removed else MISSING
        if i in d_of_removed:
            record(here.path.child(r_parts[i]), d_of_removed[i])
        _planned(found, here.at(r_parts[i], r[i], MISSING, still_there, node.item))
    partnered = {*d_of.values(), *d_of_removed.values()}
    found.server_changes.extend(
        ServerChange(here.path.child(d_parts[k]), MISSING, item)
        for k, item in enumerate(d)
        if k not in partnered
    )
    _order_change(found, here, pairing, names, d_of)


def _order_change(
    found: _Found,
    here: _Here,
    pairing: Pairing,
    names: dict[int, ItemPart],
    d_of: dict[int, int],
) -> None:
    """The order of a keyed list, as rule 3 says: planned, left out or a server change.

    *names* is the name of each item of *p* (``item_names``), and *d_of* the
    index in *d* of each item of *p* that *d* holds (``_pair_stored``).
    """
    r = cast("list[object]", here.r)
    p = cast("list[object]", here.p)
    node = cast("KeyedList", here.node)
    r_parts = item_parts(r, node)
    order_in_r = [in_r for in_r, _ in pairing.pairs]
    held = sorted(d_of)  # the items of p that d has, in the order of p
    order_in_d = [d_of[j] for j in held]
    in_payload_order = order_in_d == sorted(order_in_d)
    reordered = order_in_r != sorted(order_in_r)
    read_names = [item_text(r_parts[i]) for i in sorted(order_in_r)]
    meant_names = [item_text(names[j]) for j in range(len(p))]
    sent_names = [item_text(names[j]) for j in held]
    stored_names = [item_text(names[j]) for j in sorted(held, key=lambda j: d_of[j])]
    moves: tuple[ItemMove, ...] = ()
    if not in_payload_order:
        keep = staying(order_in_d)
        moves = tuple(
            ItemMove(names[j], place_of(names, j))
            for n, j in enumerate(held)
            if n not in keep
        )
    order = PlannedChange(here.path, read_names, meant_names, stored_names, moves=moves)
    if any(matches(here.path, pattern) for pattern in found.rewrites.own_order):
        if reordered:
            found.left_out.append(order)
        if not in_payload_order:
            found.server_changes.append(
                ServerChange(here.path, sent_names, stored_names)
            )
    elif reordered or (not in_payload_order and any(j in d_of for j in pairing.added)):
        (found.applied if in_payload_order else found.not_applied).append(order)
    elif not in_payload_order:
        found.server_changes.append(ServerChange(here.path, sent_names, stored_names))


def _alt_forms(items: list[object], node: KeyedList) -> list[KeyForm | None]:
    """The alt key of each item in string form, None for an item without one."""
    alt = node.spec.alt
    if alt is None:
        return [None] * len(items)
    return [key_form(read_path(item, node.item, alt)) for item in items]


def _compared_forms(
    forms: list[KeyForm | None], rewrites: Rewrites
) -> list[KeyForm | None]:
    """*forms* as ``_pair_stored`` compares them.

    With ``rewrites.ipv6``, each part that parses as an ``IPv6Interface`` is
    in canonical form, so two parts are one when ``_same_ipv6`` says so. Any
    other part is as it is.
    """
    if not rewrites.ipv6:
        return forms
    return [_canonical_form(form) for form in forms]


def _canonical_form(form: KeyForm | None) -> KeyForm | None:
    """*form* with each part that parses as an IPv6 interface in canonical form."""
    if isinstance(form, tuple):
        return tuple(_canonical_ipv6(part) for part in form)
    return None if form is None else _canonical_ipv6(form)


def _canonical_ipv6(text: str) -> str:
    """*text* in canonical ``IPv6Interface`` form, or as it is if it does not parse."""
    try:
        return str(IPv6Interface(text))
    except ValueError:
        return text


def _first_with(
    value: KeyForm | None, values: list[KeyForm | None], left: list[int]
) -> int | None:
    """The first index of *left* whose entry of *values* is *value*.

    None when there is none, or *value* is None: no key is the key of nothing.
    """
    if value is None:
        return None
    return next((index for index in left if values[index] == value), None)


def _pair_stored(
    here: _Here, removed: tuple[int, ...], rewrites: Rewrites
) -> tuple[dict[int, int], dict[int, int]]:
    """Pair the items of *p*, and the items of *r* the plan removed, with those of *d*.

    Returns index in *p* to index in *d*, and index in *r* to index in *d*,
    for the indexes *removed*. Four passes, each over the items still left:

    1. by alt key, and
    2. by key, each for a value that occurs once on each side, as
       ``pair_items`` does it;
    3. each removed item of *r* takes the first item of *d* that has its alt
       key. Failing that, it takes the first item of *d* that has its key, but
       only while *d* has more items left with that key than *p* has: the
       others are the partners of items the plan added under that key, which
       pass 4 pairs. An item so taken says the removal was not applied; it is
       not another writer's item;
    4. the items of *p* and *d* pair in order, first with first, while the two
       keys are equal or the item of *p* has no key. This pairs items whose
       key repeats and whose alt key the VCO revised, and an item sent without
       a key that the VCO issued one for. The first pair that fails ends the
       pass.

    Keys and alt keys are compared in string form, part by part, and with
    ``rewrites.ipv6`` a part that parses as an IPv6 interface in canonical
    form (``_compared_forms``): the VCO may store the address text of a key
    otherwise than sent. The item so paired is then walked as any other, so
    the key text it stores otherwise is a server change.
    """
    r = cast("list[object]", here.r)
    p = cast("list[object]", here.p)
    d = cast("list[object]", here.d)
    node = cast("KeyedList", here.node)
    r_keys, p_keys, d_keys = (
        _compared_forms(key_forms(items, node), rewrites) for items in (r, p, d)
    )
    r_alts, p_alts, d_alts = (
        _compared_forms(_alt_forms(items, node), rewrites) for items in (r, p, d)
    )
    d_of: dict[int, int] = {}
    p_left: list[int] = list(range(len(p)))
    d_left: list[int] = list(range(len(d)))
    for p_values, d_values in ((p_alts, d_alts), (p_keys, d_keys)):
        pair_unique(d_values, p_values, d_left, p_left, d_of)
        paired = set(d_of.values())
        p_left = [j for j in p_left if j not in d_of]
        d_left = [k for k in d_left if k not in paired]

    d_of_removed: dict[int, int] = {}
    for i in removed:
        key = r_keys[i]
        taken = _first_with(r_alts[i], d_alts, d_left)
        if taken is None and (
            sum(d_keys[k] == key for k in d_left)
            > sum(p_keys[j] == key for j in p_left)
        ):
            taken = _first_with(key, d_keys, d_left)
        if taken is not None:
            d_of_removed[i] = taken
            d_left.remove(taken)

    _pair_in_order(p_keys, d_keys, p_left, d_left, d_of)
    return d_of, d_of_removed


def _pair_in_order(
    p_keys: list[KeyForm | None],
    d_keys: list[KeyForm | None],
    p_left: list[int],
    d_left: list[int],
    d_of: dict[int, int],
) -> None:
    """Pass 4 of ``_pair_stored``: pair the items left of *p* and *d* in order.

    Each pair is written into *d_of*, index in *p* to index in *d*.
    """
    for j, k in zip(p_left, d_left, strict=False):
        if p_keys[j] is not None and p_keys[j] != d_keys[k]:
            break
        d_of[j] = k


def _planned(
    found: _Found,
    here: _Here,
    *,
    place: ItemPlace | None = None,
    moves: tuple[ItemMove, ...] | None = None,
) -> None:
    """Rule 5: one planned change at *path*, applied when *d* holds *p*.

    *place* and *moves* are the change's, as ``PlannedChange`` has them.

    The removal of a key the VCO adds a default back at (``Rewrites.readded``)
    is left out when *d* has a value there, and that value is a server change.
    """
    change = PlannedChange(here.path, here.r, here.p, here.d, place=place, moves=moves)
    if (
        here.p is MISSING
        and here.d is not MISSING
        and any(matches(here.path, pattern) for pattern in found.rewrites.readded)
    ):
        found.left_out.append(change)
        found.server_changes.append(ServerChange(here.path, here.r, here.d))
        return
    beyond = _beyond(found.rewrites, here)
    if beyond is None:
        found.not_applied.append(change)
    else:
        found.applied.append(change)
        found.server_changes.extend(beyond)


def _beyond(
    rewrites: Rewrites, here: _Here, *, top: bool = True
) -> list[ServerChange] | None:
    """What *d* has beyond *p*, or None when *d* does not hold *p*.

    *d* holds *p* when:

    - *p* is ``MISSING`` and so is *d*;
    - *p* is an object and *d* an object that holds ``p[key]`` at every key of
      *p*. *d* may have more keys, but not a key *r* had and *p* lacks, unless
      the VCO owns that key or adds it back (``Rewrites.readded``). A key the
      VCO owns always holds;
    - *p* is a list and *d* a list of the same length whose item at each
      position holds the item of *p* there;
    - otherwise, the two are equal, or with ``rewrites.ipv6`` two strings
      that parse as one IPv6 interface.

    Each key only *d* has, at any depth, is a server change at its own path,
    and so is a key the VCO owns whose value differs, and a string that holds
    only as IPv6. An item of a list is named by its position.

    *r* is the value as read at *path*: the key's value under an object, the
    item at the same position under a list, ``MISSING`` where there is none.
    It is the ``as_read`` of a server change at *path* when *top* is true.
    Below the path of the planned change nothing as read is paired with what
    *d* has, so a server change there has ``as_read`` ``MISSING``.
    """
    if is_dict(here.p):
        return _beyond_object(rewrites, here)
    if is_list(here.p):
        return _beyond_list(rewrites, here)
    if same_json(here.d, here.p):
        return []
    if rewrites.ipv6 and _same_ipv6(here.p, here.d):
        return [ServerChange(here.path, here.r if top else MISSING, here.d)]
    return None


def _beyond_object(rewrites: Rewrites, here: _Here) -> list[ServerChange] | None:
    """``_beyond`` where *p* is an object."""
    r = here.r
    p = cast("dict[object, object]", here.p)
    d = here.d
    if not is_dict(d):
        return None
    out: list[ServerChange] = []
    for key, value in p.items():
        key_path = here.path.child(str(key))
        stored = d.get(key, MISSING)
        if _is_owned(rewrites, here.node, key, key_path):
            if not same_json(value, stored):
                out.append(ServerChange(key_path, MISSING, stored))
            continue
        below = _beyond(
            rewrites,
            here.at(
                str(key),
                r.get(key, MISSING) if is_dict(r) else MISSING,
                value,
                stored,
                _child_node(here.node, key),
            ),
            top=False,
        )
        if below is None:
            return None
        out.extend(below)
    for key, value in d.items():
        if key in p:
            continue
        key_path = here.path.child(str(key))
        if (
            is_dict(r)
            and key in r
            and not _is_owned(rewrites, here.node, key, key_path)
            and not any(matches(key_path, pattern) for pattern in rewrites.readded)
        ):
            return None
        out.append(ServerChange(key_path, MISSING, value))
    return out


def _beyond_list(rewrites: Rewrites, here: _Here) -> list[ServerChange] | None:
    """``_beyond`` where *p* is a list."""
    r = here.r
    p = cast("list[object]", here.p)
    d = here.d
    if not is_list(d) or len(d) != len(p):
        return None
    item_node = _item_node(here.node)
    out: list[ServerChange] = []
    for n, (p_item, d_item) in enumerate(zip(p, d, strict=True)):
        r_item = r[n] if is_list(r) and n < len(r) else MISSING
        below = _beyond(
            rewrites, here.at(n, r_item, p_item, d_item, item_node), top=False
        )
        if below is None:
            return None
        out.extend(below)
    return out


def _same_ipv6(a: object, b: object) -> bool:
    """Whether *a* and *b* are strings that parse as one ``IPv6Interface``."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    try:
        return IPv6Interface(a) == IPv6Interface(b)
    except ValueError:
        return False
