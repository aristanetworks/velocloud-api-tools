"""The fields of a model and of the models below it, with where each one sits.

``walk_fields`` is the one walk over a module's data that code which looks for
marked fields uses (``pyvelo.core.references`` for ``RefersTo`` and
``Provides``). It goes through sub-models, lists of models (an item of a
keyed list is named by its ``Key``, any other by its position) and maps of
models (by key). A model whose class cannot hold what the caller looks for is
not walked, so a firewall of thousands of rules costs nothing to look through.

The one list that holds segment entries is the root's ``segments``: the
fields below one of its items come with that item as their ``segment``.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from pydantic.fields import FieldInfo

from .base import VcoModel, is_dict, is_list
from .fields import get_key_spec
from .rebase import Key, Path, item_key


@dataclass(frozen=True)
class FieldSite:
    """A field of a model, where ``walk_fields`` found it."""

    model: VcoModel
    """The model that holds the field."""
    name: str
    """The field's python name."""
    field_info: FieldInfo
    path: Path
    """The field's path in python names: ``segments["Global Segment"].dns.primary_provider``."""
    segment: VcoModel | None
    """The item of the root's ``segments`` list that holds the field, else ``None``."""


def walk_fields(
    data: VcoModel, *, holds: Callable[[type[VcoModel]], bool]
) -> Iterator[FieldSite]:
    """Every field of *data* and of the models below it, in field order.

    *holds* says whether a class is worth walking: a model of a class it
    rejects gives no field and is not looked into. It is asked once per class.
    """
    answers: dict[type[VcoModel], bool] = {}

    def wanted(model: VcoModel) -> bool:
        model_cls = type(model)
        if model_cls not in answers:
            answers[model_cls] = holds(model_cls)
        return answers[model_cls]

    def walk(
        model: VcoModel, path: Path, segment: VcoModel | None, *, root: bool
    ) -> Iterator[FieldSite]:
        if not wanted(model):
            return
        for name, field_info in type(model).model_fields.items():
            value: object = getattr(model, name)
            here = path.child(name)
            yield FieldSite(model, name, field_info, here, segment)
            if isinstance(value, VcoModel):
                yield from walk(value, here, segment, root=False)
            elif is_list(value):
                spec = get_key_spec(field_info)
                for position, item in enumerate(value):
                    if not isinstance(item, VcoModel) or not wanted(item):
                        continue
                    key = item_key(item, spec.paths) if spec is not None else None
                    part = Key(key) if key is not None else position
                    below = item if root and name == "segments" else segment
                    yield from walk(item, here.child(part), below, root=False)
            elif is_dict(value):
                for key, item in value.items():
                    if isinstance(item, VcoModel) and wanted(item):
                        yield from walk(item, here.child(str(key)), segment, root=False)

    yield from walk(data, Path(()), None, root=True)
