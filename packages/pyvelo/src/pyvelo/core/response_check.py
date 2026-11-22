"""The response check: compare what the VCO stored with what a write meant to change.

Three JSON documents are walked together: the data as read ``R``, the payload
``P`` that was sent, and the data ``D`` the VCO stored. A difference between
``R`` and ``P`` is a change the plan made; ``D`` says whether the VCO applied
it. A difference between ``P`` and ``D`` anywhere else is a change the VCO
made on its own.

At each place the first of these rules that fits is used. ``MISSING`` stands
for a side that has no value there.

1. ``R`` equals ``P``: nothing is planned here. Every difference between ``P``
   and ``D`` (``raw_changes``) is a server change.
2. All three are objects: each key is walked, those of ``R`` first, then those
   only ``P`` has, then those only ``D`` has.
3. A keyed list whose items ``pair_items`` pairs between ``R`` and ``P``, and
   ``D`` is a list: the items of ``P`` are paired with those of ``D``
   (``_pair_stored``), and each is walked with its partners. An item the plan
   removed is one planned change, and an item only ``D`` has is one server
   change. The order is one more change, at the list, told by the items' keys.
4. ``R`` and ``P`` are objects, or such a keyed list, and ``D`` is not: every
   difference between ``R`` and ``P`` is a planned change with nothing stored
   at its path, so a removal is applied and any other change is not. What
   ``D`` has in place of the container is one server change.
5. Anything else is one planned change: a scalar that was set, an object that
   was set or added, a list replaced whole (a list without a key, or a keyed
   list ``pair_items`` cannot pair), a key or an item that was removed. It is
   applied when ``D`` holds the payload's value (``_beyond``).

The optional *model* is the ``VcoModel`` class the documents are data of. It
supplies the ``KeyField`` of each list, as it does for ``rebase``. With
``model=None`` every list is unkeyed, so a changed list is one change.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import MISSING

from .base import (
    VcoModel,
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
)
from .rebase import (
    Key,
    KeyForm,
    Pairing,
    Path,
    PathPart,
    RawChange,
    _child,  # pyright: ignore[reportPrivateUsage]
    _collect_changes,  # pyright: ignore[reportPrivateUsage]
    _item_parts,  # pyright: ignore[reportPrivateUsage]
    _key_forms,  # pyright: ignore[reportPrivateUsage]
    _KeyedList,  # pyright: ignore[reportPrivateUsage]
    _Node,  # pyright: ignore[reportPrivateUsage]
    _pair_unique,  # pyright: ignore[reportPrivateUsage]
    _read_path,  # pyright: ignore[reportPrivateUsage]
    key_form,
    key_text,
    pair_items,
)


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One change the plan made, and what the VCO holds there now."""

    path: Path
    as_read: object  # MISSING: nothing was there
    intended: object  # MISSING: a removal
    stored: object  # MISSING: nothing is there
    part: Literal["data", "refs"] = "data"

    @property
    def clash(self) -> bool:
        """The stored value is not the one read: something else changed it too."""
        return self.stored != self.as_read


@dataclass(frozen=True, slots=True)
class ServerChange:
    """A difference between the payload and what is stored, outside the plan's changes."""

    path: Path
    as_read: object
    stored: object
    part: Literal["data", "refs"] = "data"


@dataclass(frozen=True, slots=True)
class ResponseCheck:
    """What ``check_response`` found."""

    applied: tuple[PlannedChange, ...] = ()
    """The planned changes the VCO stored."""
    not_applied: tuple[PlannedChange, ...] = ()
    """The planned changes it did not: the old value is there, or another one."""
    left_out: tuple[PlannedChange, ...] = ()
    """The planned changes that count as neither. Nothing is left out yet."""
    server_changes: tuple[ServerChange, ...] = ()
    """What else differs between the payload and what is stored."""


def check_response(
    as_read: object,
    payload: object,
    stored: object,
    model: type[VcoModel] | None = None,
) -> ResponseCheck:
    """Compare what the VCO stored with what a write meant to change.

    *as_read* is the data the payload was made from, *payload* the data that
    was sent, and *stored* the data the VCO returned for the write (or a read
    made after it). All three are JSON, of *model* when one is given.

    Every difference between *as_read* and *payload* is a planned change, at
    the deepest path that can be told apart (the items of a keyed list by
    their key, as ``raw_changes`` pairs them). It is applied when *stored*
    holds the payload's value there: the same scalar; for an object, every
    leaf of the payload's (*stored* may hold more keys); for a list replaced
    whole, the payload's items in the payload's order; nothing, for a removal.
    A reorder of a keyed list is one change at the list, whose three values
    are the items' keys in each document, and an edit inside an item is
    another. Whatever else differs between *payload* and *stored* is a server
    change.
    """
    found = _Found()
    _walk(found, Path(()), as_read, payload, stored, model)
    return ResponseCheck(
        applied=tuple(found.applied),
        not_applied=tuple(found.not_applied),
        server_changes=tuple(found.server_changes),
    )


@dataclass(slots=True)
class _Found:
    """The changes the walk has found so far."""

    applied: list[PlannedChange] = field(default_factory=list)
    not_applied: list[PlannedChange] = field(default_factory=list)
    server_changes: list[ServerChange] = field(default_factory=list)


def _differences(path: Path, old: object, new: object, node: _Node) -> list[RawChange]:
    """What ``raw_changes`` lists between *old* and *new*, with *path* before each path.

    ``raw_changes`` starts at a model; this starts at any node, a keyed list
    included.
    """
    out: list[RawChange] = []
    _collect_changes(out, path, old, new, node)
    return out


def _walk(
    found: _Found, path: Path, r: object, p: object, d: object, node: _Node
) -> None:
    if r == p:
        # Rule 1: nothing planned here.
        found.server_changes.extend(
            ServerChange(change.path, change.before, change.after)
            for change in _differences(path, p, d, node)
        )
    elif _is_dict(r) and _is_dict(p):
        if _is_dict(d):
            _walk_object(found, path, r, p, d, node)
        else:
            _walk_replaced(found, path, r, p, d, node)
    elif (
        isinstance(node, _KeyedList)
        and _is_list(r)
        and _is_list(p)
        and (pairing := pair_items(r, p, node)) is not None
    ):
        if _is_list(d):
            _walk_keyed_list(found, path, r, p, d, node, pairing)
        else:
            _walk_replaced(found, path, r, p, d, node)
    else:
        _planned(found, path, r, p, d, node)


def _walk_object(
    found: _Found,
    path: Path,
    r: dict[object, object],
    p: dict[object, object],
    d: dict[object, object],
    node: _Node,
) -> None:
    """Rule 2: walk each key of *r*, then those only *p* has, then those only *d* has."""
    keys = [
        *r,
        *(key for key in p if key not in r),
        *(key for key in d if key not in r and key not in p),
    ]
    for key in keys:
        _walk(
            found,
            path.child(str(key)),
            r.get(key, MISSING),
            p.get(key, MISSING),
            d.get(key, MISSING),
            _child(node, key),
        )


def _walk_replaced(
    found: _Found, path: Path, r: object, p: object, d: object, node: _Node
) -> None:
    """Rule 4: *d* is not the container *r* and *p* are, so it stores nothing inside it.

    Each difference between *r* and *p* is a planned change that meets nothing
    stored. By rule 5's test that applies a removal, and nothing else.
    """
    for change in _differences(path, r, p, node):
        _planned(found, change.path, change.before, change.after, MISSING, None)
    found.server_changes.append(ServerChange(path, r, d))


def _walk_keyed_list(
    found: _Found,
    path: Path,
    r: list[object],
    p: list[object],
    d: list[object],
    node: _KeyedList,
    pairing: Pairing,
) -> None:
    """Rule 3: the items of a keyed list, each with its partners, and then the order."""
    r_of = {in_p: in_r for in_r, in_p in pairing.pairs}
    d_of, d_of_removed = _pair_stored(r, p, d, node, pairing.removed)
    r_parts, p_parts, d_parts = (_item_parts(items, node) for items in (r, p, d))
    for j, item in enumerate(p):
        _walk(
            found,
            path.child(p_parts[j]),
            r[r_of[j]] if j in r_of else MISSING,
            item,
            d[d_of[j]] if j in d_of else MISSING,
            node.item,
        )
    for i in pairing.removed:
        still_there = d[d_of_removed[i]] if i in d_of_removed else MISSING
        _planned(found, path.child(r_parts[i]), r[i], MISSING, still_there, node.item)
    partnered = {*d_of.values(), *d_of_removed.values()}
    found.server_changes.extend(
        ServerChange(path.child(d_parts[k]), MISSING, item)
        for k, item in enumerate(d)
        if k not in partnered
    )

    order_in_r = [in_r for in_r, _ in pairing.pairs]
    order_in_d = [d_of[j] for j in sorted(d_of)]
    in_payload_order = order_in_d == sorted(order_in_d)
    if order_in_r != sorted(order_in_r):
        reorder = PlannedChange(
            path, _key_texts(r_parts), _key_texts(p_parts), _key_texts(d_parts)
        )
        (found.applied if in_payload_order else found.not_applied).append(reorder)
    elif not in_payload_order:
        found.server_changes.append(
            ServerChange(path, _key_texts(p_parts), _key_texts(d_parts))
        )


def _key_texts(parts: list[PathPart]) -> list[str]:
    """Each item of a list as its path names it: its key, or ``#n`` for a position."""
    return [
        key_text(part.value) if isinstance(part, Key) else f"#{part}" for part in parts
    ]


def _alt_forms(items: list[object], node: _KeyedList) -> list[KeyForm | None]:
    """The alt key of each item in string form, None for an item without one."""
    alt = node.spec.alt
    if alt is None:
        return [None] * len(items)
    return [key_form(_read_path(item, node.item, alt)) for item in items]


def _is_same(value: KeyForm | None, other: KeyForm | None) -> bool:
    """Whether two keys in string form are one key. No key is the same as nothing."""
    return value is not None and value == other


def _pair_stored(
    r: list[object],
    p: list[object],
    d: list[object],
    node: _KeyedList,
    removed: tuple[int, ...],
) -> tuple[dict[int, int], dict[int, int]]:
    """Pair the items of *p*, and the items of *r* the plan removed, with those of *d*.

    Returns index in *p* to index in *d*, and index in *r* to index in *d*,
    for the indexes *removed*. Four passes, each over the items still left:

    1. by alt key, and
    2. by key, each for a value that occurs once on each side, as
       ``pair_items`` does it;
    3. each removed item of *r* takes the first item of *d* that has its key
       or its alt key: the removal was not applied, and the item is not
       another writer's, nor the partner of an item the plan added;
    4. the items of *p* and *d* pair in order, first with first, while the two
       keys are equal or the item of *p* has no key. This pairs items whose
       key repeats and whose alt key the VCO revised, and an item sent without
       a key that the VCO issued one for. The first pair that fails ends the
       pass.

    Keys and alt keys are compared in string form.
    """
    p_keys, d_keys = _key_forms(p, node), _key_forms(d, node)
    p_alts, d_alts = _alt_forms(p, node), _alt_forms(d, node)
    d_of: dict[int, int] = {}
    p_left: list[int] = list(range(len(p)))
    d_left: list[int] = list(range(len(d)))
    for p_values, d_values in ((p_alts, d_alts), (p_keys, d_keys)):
        _pair_unique(d_values, p_values, d_left, p_left, d_of)
        paired = set(d_of.values())
        p_left = [j for j in p_left if j not in d_of]
        d_left = [k for k in d_left if k not in paired]

    r_keys, r_alts = _key_forms(r, node), _alt_forms(r, node)
    d_of_removed: dict[int, int] = {}
    for i in removed:
        for k in d_left:
            if _is_same(r_keys[i], d_keys[k]) or _is_same(r_alts[i], d_alts[k]):
                d_of_removed[i] = k
                d_left.remove(k)
                break

    for j, k in zip(p_left, d_left, strict=False):
        if p_keys[j] is not None and p_keys[j] != d_keys[k]:
            break
        d_of[j] = k
    return d_of, d_of_removed


def _planned(
    found: _Found, path: Path, r: object, p: object, d: object, node: _Node
) -> None:
    """Rule 5: one planned change at *path*, applied when *d* holds *p*."""
    change = PlannedChange(path, r, p, d)
    beyond = _beyond(path, p, d, node)
    if beyond is None:
        found.not_applied.append(change)
    else:
        found.applied.append(change)
        found.server_changes.extend(beyond)


def _beyond(path: Path, p: object, d: object, node: _Node) -> list[ServerChange] | None:
    """What *d* has beyond *p*, or None when *d* does not hold *p*.

    *d* holds *p* when:

    - *p* is ``MISSING`` and so is *d*;
    - *p* is an object and *d* an object that holds ``p[key]`` at every key of
      *p*. *d* may have more keys;
    - *p* is a list and *d* a list of the same length whose item at each
      position holds the item of *p* there;
    - otherwise, the two are equal.

    Each key only *d* has, at any depth, is a server change at its own path.
    An item of a list is named by its position.
    """
    if _is_dict(p):
        if not _is_dict(d):
            return None
        out: list[ServerChange] = []
        for key, value in p.items():
            below = _beyond(
                path.child(str(key)), value, d.get(key, MISSING), _child(node, key)
            )
            if below is None:
                return None
            out.extend(below)
        out.extend(
            ServerChange(path.child(str(key)), MISSING, value)
            for key, value in d.items()
            if key not in p
        )
        return out
    if _is_list(p):
        if not _is_list(d) or len(d) != len(p):
            return None
        item_node = node.item if isinstance(node, _KeyedList) else None
        out = []
        for n, (p_item, d_item) in enumerate(zip(p, d, strict=True)):
            below = _beyond(path.child(n), p_item, d_item, item_node)
            if below is None:
                return None
            out.extend(below)
        return out
    return [] if d == p else None
