"""The fields of a model and of the models below it, with where each one sits.

``walk_fields`` is the one walk over a module's data that code which looks for
marked fields uses (``pyvelo.core.references`` for ``RefersTo`` and
``Provides``). It goes through sub-models, lists of models (an item of a
keyed list is named by its ``Key``, any other by its position) and maps of
models (by key). A model whose class cannot hold what the caller looks for is
not walked, so a firewall of thousands of rules costs nothing to look through.

The one list that holds segment entries is the root's ``segments``: the
fields below one of its items come with that item as their ``segment``.

What a class can hold is read from the classes alone: ``models_below`` is
every model class below one, and ``can_hold`` and ``can_hold_field`` are the
two questions a walk's ``holds`` asks of it (whether a class can hold a
model of a given class, or a field a predicate accepts).
"""

import functools
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast

from pydantic.fields import FieldInfo

from .base import VcoModel, field_models, is_dict, is_list
from .fields import get_key_spec
from .owned import unwrap
from .rebase import Key, Path, item_key


@functools.cache
def models_below(model_cls: type[VcoModel]) -> tuple[type[VcoModel], ...]:
    """*model_cls* and every model class a model of it can hold, at any depth.

    *model_cls* comes first, and each class once. The classes are read from
    the declared field types (``field_models``: through ``Annotated``, unions,
    ``list``, ``dict`` and ``tuple``), so a subclass that adds a field below a
    field declared with a class that has none is not found. Computed once per
    class.
    """
    found: dict[type[VcoModel], None] = {}
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found[current] = None
        for field_info in current.model_fields.values():
            pending.extend(field_models(field_info))
    return tuple(found)


def can_hold(model_cls: type[VcoModel], target: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* is a *target* or can hold one, at any depth."""
    return target in models_below(model_cls)


def can_hold_field(
    model_cls: type[VcoModel], marked: Callable[[FieldInfo], bool]
) -> bool:
    """Whether a model of *model_cls*, or one below it, has a field *marked* accepts.

    The model below may be at any depth.
    """
    return any(
        marked(field_info)
        for below in models_below(model_cls)
        for field_info in below.model_fields.values()
    )


@dataclass(frozen=True)
class FieldSite:
    """A field of a model, where ``walk_fields`` found it."""

    model: VcoModel
    """The model that holds the field."""
    name: str
    """The field's python name."""
    field_info: FieldInfo
    path: Path
    """The field's path in python names.

    For example, ``segments["Global Segment"].dns.primary_provider``.
    """
    segment: VcoModel | None
    """The item of the root's ``segments`` list that holds the field, else ``None``."""


def walk_fields(  # noqa: C901 (existing: split it when it next changes)
    data: VcoModel,
    *,
    holds: Callable[[type[VcoModel]], bool],
    fields: Callable[[FieldInfo], bool] | None = None,
) -> Iterator[FieldSite]:
    """Every field of *data* and of the models below it, in field order.

    *holds* says whether a class is worth walking: a model of a class it
    rejects gives no field and is not looked into. It is asked once per class.

    *fields* says which fields are wanted as sites, and is asked once per
    field of a class; without it every field is. A field that is not wanted
    gives no site and no path: only the models, lists and maps below it are
    looked into, so a walk for a few marked fields among thousands of scalars
    builds a path for the marked ones and for the containers that lead to them.
    """
    answers: dict[type[VcoModel], bool] = {}
    layouts: dict[type[VcoModel], tuple[tuple[str, FieldInfo, bool], ...]] = {}

    def wanted(model: VcoModel) -> bool:
        model_cls = type(model)
        if model_cls not in answers:
            answers[model_cls] = holds(model_cls)
        return answers[model_cls]

    def layout(model: VcoModel) -> tuple[tuple[str, FieldInfo, bool], ...]:
        model_cls = type(model)
        if model_cls not in layouts:
            layouts[model_cls] = tuple(
                (name, field_info, fields is None or fields(field_info))
                for name, field_info in model_cls.model_fields.items()
            )
        return layouts[model_cls]

    def walk(  # noqa: C901 (existing: split it when it next changes)
        model: VcoModel, path: Path, segment: VcoModel | None, *, root: bool
    ) -> Iterator[FieldSite]:
        if not wanted(model):
            return
        for name, field_info, asked in layout(model):
            held: object = getattr(model, name)
            value = unwrap(held)
            below_model = isinstance(value, VcoModel)
            listed = not below_model and is_list(value)
            mapped = not below_model and not listed and is_dict(value)
            if not (asked or below_model or listed or mapped):
                continue
            here = path.child(name)
            if asked:
                yield FieldSite(model, name, field_info, here, segment)
            if below_model:
                yield from walk(value, here, segment, root=False)
            elif listed:
                spec = get_key_spec(field_info)
                for position, item in enumerate(cast("list[object]", value)):
                    if not isinstance(item, VcoModel) or not wanted(item):
                        continue
                    key = item_key(item, spec.paths) if spec is not None else None
                    part = Key(key) if key is not None else position
                    inside = item if root and name == "segments" else segment
                    yield from walk(item, here.child(part), inside, root=False)
            elif mapped:
                for key, item in cast("dict[object, object]", value).items():
                    if isinstance(item, VcoModel) and wanted(item):
                        yield from walk(item, here.child(str(key)), segment, root=False)

    yield from walk(data, Path(()), None, root=True)
