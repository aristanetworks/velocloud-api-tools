"""Show configuration with its secrets left out.

A field marked ``Secret`` holds a password, a key or a community string.
``hide_secrets`` turns a value about to be shown (in a plan, an error or a
dump) into one that is safe to show.
"""

from typing import Final, TypeIs

from pydantic import MISSING

from .base import (
    VcoModel,
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
    field_models,
    list_kind,
    python_name,
)
from .fields import is_secret
from .rebase import Path

HIDDEN: Final = "***"

type _Classes = tuple[type[VcoModel], ...]


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _under(classes: _Classes, key: object) -> tuple[bool, _Classes, bool] | None:
    """The field JSON key *key* names in *classes*: is it secret, what it holds, is it a map.

    None when no class has a field read from *key*. With several classes a
    field is secret if it is in any of them, and holds what it holds in each.
    """
    if not isinstance(key, str):
        return None
    known = secret = is_map = False
    below: list[type[VcoModel]] = []
    for cls in classes:
        name = python_name(cls, key)
        if name is None:
            continue
        known = True
        field_info = cls.model_fields[name]
        secret = secret or is_secret(field_info)
        is_map = is_map or list_kind(field_info) == "map"
        below.extend(m for m in field_models(field_info) if m not in below)
    return (secret, tuple(below), is_map) if known else None


def _hide(value: object, classes: _Classes, is_map: bool) -> object:
    if not classes:
        return value
    if _is_list(value) or _is_tuple(value):
        return [_hide(item, classes, is_map) for item in value]
    if not _is_dict(value):
        return value
    if is_map:  # the keys are map keys, not field names
        return {key: _hide(item, classes, False) for key, item in value.items()}
    hidden: dict[object, object] = {}
    for key, item in value.items():
        found = _under(classes, key)
        if found is None:
            hidden[key] = item
        elif found[0]:
            hidden[key] = HIDDEN
        else:
            hidden[key] = _hide(item, found[1], found[2])
    return hidden


def hide_secrets(model: type[VcoModel] | None, path: Path, value: object) -> object:
    """*value*, the JSON at *path* of a document of *model*, with the secrets hidden.

    The string ``"***"`` stands in place of the value of every field marked
    ``Secret``, at any depth, and is the result when *path* is at or below such
    a field. A tuple is walked as a list is. *value* is returned as it is when
    it is ``MISSING``, when *model* is None, and where the model does not know
    the place. The value given is never changed.
    """
    if model is None or value is MISSING:
        return value
    classes: _Classes = (model,)
    is_map = False
    for part in path.parts:
        if not isinstance(part, str):
            continue  # an item of a list: the same classes
        if is_map:
            is_map = False  # an entry of a map: the same classes
            continue
        found = _under(classes, part)
        if found is None:
            return value
        secret, classes, is_map = found
        if secret:
            return HIDDEN
    return _hide(value, classes, is_map)
