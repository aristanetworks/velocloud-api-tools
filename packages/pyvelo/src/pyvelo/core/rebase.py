"""Three-way JSON engine: apply the change ``before -> after`` to ``raw``.

``raw`` is a JSON document exactly as it was read. ``before`` is the wire dump
of the model parsed from it, and ``after`` the wire dump of that model now.
``rebase`` carries only the difference between ``before`` and ``after`` over to
``raw``, so what parsing lost (unknown keys, coerced values, defaults the dump
added) never reaches the result. ``raw_changes`` lists the differences between
two JSON documents, with the same addressing.

The optional *model* is the ``VcoModel`` class whose wire dump ``before`` and
``after`` are. It supplies two things that JSON alone cannot:

- the ``KeyField`` of each list, so keyed lists are matched by key and not by
  position. With ``model=None`` every list is unkeyed.
- the other keys a field is read from (``logging_enabled`` for
  ``loggingEnabled``), so a change lands on the key as read and does not add a
  second one.

The result of ``rebase`` may share sub-objects with its inputs. Callers copy.
"""

from dataclasses import dataclass

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import (
    VcoModel,
    _field_alias,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
    _list_item_type,  # pyright: ignore[reportPrivateUsage]
)
from .fields import get_key_field


@dataclass(frozen=True, slots=True)
class RawChange:
    """One difference between two JSON documents.

    ``MISSING`` stands for the side that has no value at ``path``.
    """

    path: str
    before: object
    after: object


@dataclass(frozen=True, slots=True)
class _KeyedList:
    """A list whose items are matched by the value at ``key_path``."""

    key_path: str
    """Dotted path of python field names, as in ``KeyField``."""
    item: type[VcoModel]


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

    A keyed list for a ``KeyField`` list of models (of the first variant, for a
    discriminated union), a model class for a model field, else None.
    """
    found = _model_field(node, key)
    if found is None:
        return None
    field_info = found[1]
    key_path = get_key_field(field_info)
    item_type = _list_item_type(field_info)
    if key_path is not None and item_type is not None:
        return _KeyedList(key_path, item_type.variants[0])
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
    have the same length. *before* and *after* are paired by key.
    """
    if len(raw) != len(before):
        return None
    before_keys = _list_keys(before, node)
    after_keys = _list_keys(after, node)
    if before_keys is None or after_keys is None:
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
    _collect_changes(changes, "", old, new, model)
    return changes


def _join(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


def _collect_changes(
    out: list[RawChange], path: str, old: object, new: object, node: _Node
) -> None:
    if old == new:
        return
    if _is_dict(old) and _is_dict(new):
        for key, old_value in old.items():
            if key in new:
                _collect_changes(
                    out, _join(path, key), old_value, new[key], _child(node, key)
                )
            else:
                out.append(RawChange(_join(path, key), old_value, MISSING))
        out.extend(
            RawChange(_join(path, key), MISSING, new_value)
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
    path: str,
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
                out, f"{path}[{key}]", old_item, new_by_key[key], node.item
            )
        else:
            out.append(RawChange(f"{path}[{key}]", old_item, MISSING))
    out.extend(
        RawChange(f"{path}[{key}]", MISSING, new_item)
        for key, new_item in new_by_key.items()
        if key not in old_by_key
    )
