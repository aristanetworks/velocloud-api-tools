"""Find where a payload changes the fixed part of a ``FixedList``.

A change is found relative to a baseline.

``fixed_list_violations`` walks raw JSON, a baseline and the payload planned
from it, along the model class: sub-models, keyed lists of models (their items
paired as a save pairs them, ``pair_items``) and maps of models. At each field
marked ``FixedList`` it compares the two lists by position. It is the
authoritative check: whatever made the payload (an attribute assignment, a
plain-dict data, a patch), a change to a fixed part shows here.

A class that cannot reach a ``FixedList`` is not walked, as ``ref_owners`` does
for owners: that is computed once per class (``holds_fixed_list``).
"""

import functools
from collections.abc import Iterator

from pyvelo.core.base import VcoModel, is_dict, is_list, same_json
from pyvelo.core.fields import FixedList, get_fixed_list
from pyvelo.core.rebase import (
    KeyedList,
    ModelMap,
    Node,
    Path,
    item_parts,
    key_forms,
    node_child,
    node_field,
    pair_items,
    wire_keys,
)
from pyvelo.core.walk import can_hold_field

LIST_GONE = "the list is gone"
LIST_BUILT = "a list built where there was none"
LIST_CHANGED = "an item added, removed, renamed or moved"
_ABSENT = object()


@functools.cache
def holds_fixed_list(model_cls: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* can hold a ``FixedList`` field, at any depth.

    True when one of its fields carries a ``FixedList``, or can hold a model
    class that can (through ``Annotated``, unions, ``list`` and ``dict``). It is
    computed from the declared field types, once per class.
    """
    return can_hold_field(model_cls, lambda info: get_fixed_list(info) is not None)


def _node_model(node: Node) -> type[VcoModel] | None:
    """The model class that a node's values are of, or None for no node."""
    if isinstance(node, KeyedList):
        return node.item
    if isinstance(node, ModelMap):
        return node.value
    return node


def _by_field(model: type[VcoModel], value: object) -> dict[str, tuple[str, object]]:
    """The fields of the JSON object *value* by python name.

    Each is ``(key it is read from, its value)``.

    A key the model does not know is left out; of two keys for one field the
    first is taken. Anything but an object has no fields.
    """
    found: dict[str, tuple[str, object]] = {}
    if not is_dict(value):
        return found
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        field = node_field(model, key)
        if field is not None and field[0] not in found:
            found[field[0]] = (key, item)
    return found


def _walk(
    node: Node, baseline: object, payload: object, path: Path
) -> Iterator[tuple[Path, str]]:
    model = _node_model(node)
    if model is None or not holds_fixed_list(model):
        return
    if isinstance(node, ModelMap):
        yield from _walk_map(node, baseline, payload, path)
    elif isinstance(node, KeyedList):
        yield from _walk_list(node, baseline, payload, path)
    elif isinstance(node, type):
        yield from _walk_model(node, baseline, payload, path)


def _walk_model(
    model: type[VcoModel], baseline: object, payload: object, path: Path
) -> Iterator[tuple[Path, str]]:
    base_fields = _by_field(model, baseline)
    pay_fields = _by_field(model, payload)
    for name in dict.fromkeys([*pay_fields, *base_fields]):
        base_key, base_value = base_fields.get(name, ("", None))
        pay_key, pay_value = pay_fields.get(name, ("", None))
        key = pay_key or base_key
        here = path.child(key)
        field_info = model.model_fields[name]
        fixed = get_fixed_list(field_info)
        child = node_child(model, key)
        if fixed is not None and isinstance(child, KeyedList):
            yield from _check_fixed(fixed, child, base_value, pay_value, here)
        else:
            yield from _walk(child, base_value, pay_value, here)


def _walk_map(
    node: ModelMap, baseline: object, payload: object, path: Path
) -> Iterator[tuple[Path, str]]:
    base = baseline if is_dict(baseline) else {}
    pay = payload if is_dict(payload) else {}
    for key, value in pay.items():
        yield from _walk(node.value, base.get(key), value, path.child(str(key)))


def _walk_list(
    node: KeyedList, baseline: object, payload: object, path: Path
) -> Iterator[tuple[Path, str]]:
    pay = payload if is_list(payload) else []
    base = baseline if is_list(baseline) else []
    pairing = pair_items(base, pay, node)
    if pairing is None:  # the items cannot be told apart: pair by position
        pairs = tuple((i, i) for i in range(min(len(base), len(pay))))
        added = tuple(range(len(base), len(pay)))
    else:
        pairs, added = pairing.pairs, pairing.added
    base_parts = item_parts(base, node)
    pay_parts = item_parts(pay, node)
    old_of = {new: old for old, new in pairs}
    for j, item in enumerate(pay):
        if j in old_of:
            yield from _walk(
                node.item, base[old_of[j]], item, path.child(base_parts[old_of[j]])
            )
        elif j in added:
            yield from _walk(node.item, None, item, path.child(pay_parts[j]))


def _field_name(item: Node, key: object) -> object:
    """The attribute the items of a list read the JSON key *key* as.

    *key* itself where they read it as none (a key the model does not declare).
    """
    found = node_field(item, key)
    return key if found is None else found[0]


def _check_fixed(
    fixed: FixedList,
    node: KeyedList,
    baseline: object,
    payload: object,
    path: Path,
) -> Iterator[tuple[Path, str]]:
    """The violations at one ``FixedList`` field, *path*.

    The lists are compared by position.
    """
    if not is_list(baseline) and not is_list(payload):
        return
    if not is_list(payload):
        yield path, LIST_GONE
        return
    if not is_list(baseline):
        yield path, LIST_BUILT
        return
    if len(baseline) != len(payload) or key_forms(baseline, node) != key_forms(
        payload, node
    ):
        yield path, LIST_CHANGED
        return
    editable = {key for name in fixed.editable for key in wire_keys(node.item, name)}
    for part, old, new in zip(
        item_parts(baseline, node), baseline, payload, strict=True
    ):
        if not is_dict(old) or not is_dict(new):
            if not same_json(old, new):
                yield path.child(part), "differs"
            continue
        kept_old = {k: v for k, v in old.items() if k not in editable}
        kept_new = {k: v for k, v in new.items() if k not in editable}
        if not same_json(kept_old, kept_new):
            first = next(
                k
                for k in {**kept_old, **kept_new}
                if not same_json(kept_old.get(k, _ABSENT), kept_new.get(k, _ABSENT))
            )
            yield path.child(part), f"{_field_name(node.item, first)} differs"


def fixed_list_violations(
    model: type[VcoModel], baseline: object, payload: object
) -> list[tuple[Path, str]]:
    """Each place where *payload* changes a fixed part of a ``FixedList`` of *baseline*.

    Each is given as ``(path, reason)``.

    *baseline* and *payload* are raw JSON of *model*; the paths are raw too,
    at the item or at the field. At a ``FixedList`` field the two lists are
    compared by position. Where both have the list and agree, or neither has
    it, there is nothing. Otherwise the reason is one of:

    - ``"the list is gone"``: the baseline has it and the payload lacks it;
    - ``"a list built where there was none"``: the payload has it and the
      baseline lacks it (for an item paired, or one the payload alone has);
    - ``"an item added, removed, renamed or moved"``, at the field: the lists
      differ in length, or in the key at some position;
    - ``"<field> differs"``, at the item: an item differs outside the editable
      fields, compared with their wire keys removed. The field is named by
      its python attribute; a key the model does not declare by itself.

    Only ``FixedList`` fields are looked at: a change anywhere else is none.
    """
    return list(_walk(model, baseline, payload, Path(())))
