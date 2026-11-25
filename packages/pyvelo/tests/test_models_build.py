"""Every pydantic model in pyvelo builds with the imports its module has at runtime.

ruff moves an import used only in annotations under ``if TYPE_CHECKING:``,
unless the class it serves derives from a base listed in pyproject.toml's
``runtime-evaluated-base-classes``. A model whose base is missing from that list
can lose an import its fields need, and fail only when it is first built. This
test builds every model, and each generic model once per constraint of its type
parameters, so such a model fails here.
"""

import importlib
import itertools
import pkgutil
from types import ModuleType

import pytest
import pyvelo
from pydantic import BaseModel


def _modules() -> list[ModuleType]:
    names = [m.name for m in pkgutil.walk_packages(pyvelo.__path__, "pyvelo.")]
    return [pyvelo, *(importlib.import_module(name) for name in names)]


def _models() -> list[type[BaseModel]]:
    found: dict[type[BaseModel], None] = {}

    def collect(namespace: dict[str, object], module: str) -> None:
        for value in list(namespace.values()):
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module
                and value not in found
            ):
                found[value] = None
                collect(dict(vars(value)), module)

    for module in _modules():
        collect(dict(vars(module)), module.__name__)
    for cls in list(found):
        parameters = cls.__pydantic_generic_metadata__["parameters"]
        choices: list[tuple[object, ...]] = [
            p.__constraints__ or ((p.__bound__,) if p.__bound__ else (object,))
            for p in parameters
        ]
        if not choices:
            continue
        for args in itertools.product(*choices):
            found[cls[args]] = None
    return sorted(found, key=lambda c: f"{c.__module__}.{c.__qualname__}")


@pytest.mark.parametrize(
    "model", _models(), ids=lambda c: f"{c.__module__}.{c.__qualname__}"
)
def test_the_model_builds(model: type[BaseModel]) -> None:
    _ = model.model_rebuild(force=True)
    assert model.__pydantic_complete__
