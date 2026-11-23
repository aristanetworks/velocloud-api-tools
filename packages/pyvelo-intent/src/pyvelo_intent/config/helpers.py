"""Small composable helpers for VCO configuration scripts."""

from collections.abc import Hashable
from pathlib import Path
from typing import cast, override

import yaml
from pydantic import BaseModel, ValidationError

from pyvelo_intent.config.state import ConfigPlan
from pyvelo_intent.errors import ConfigInputError


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a key given twice in one mapping."""

    @override
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False):
        seen: set[object] = set()
        pairs = cast(list[tuple[yaml.Node, yaml.Node]], node.value)
        for key_node, _ in pairs:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = cast(object, self.construct_object(key_node, deep=True))  # pyright: ignore[reportUnknownMemberType]
            if isinstance(key, Hashable) and key in seen:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"the key {key!r} is given twice",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _location(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(top level)"


def load_config[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Load a YAML file and validate it against a Pydantic model.

    Raises ``ConfigInputError`` for a file that is not valid YAML, that gives
    a key twice in one mapping, or that the model refuses. The message names
    the file and each field at fault, not the values, which may be secrets.
    An ``OSError`` from opening the file is left as it is.
    """
    with open(path) as f:
        try:
            data = cast(object, yaml.load(f, Loader=_UniqueKeyLoader))
        except yaml.YAMLError as error:
            raise ConfigInputError(f"{path}: not valid YAML: {error}") from error
    try:
        return model.model_validate(data)
    except ValidationError as error:
        errors = error.errors(include_input=False, include_url=False)
        lines = "; ".join(f"{_location(e['loc'])}: {e['msg']}" for e in errors)
        raise ConfigInputError(
            f"{path}: {len(errors)} invalid value(s) for {model.__name__}: {lines}"
        ) from error


def confirm_save(
    message: str = "Apply changes?",
    auto_yes: bool = False,
    *,
    plan: ConfigPlan | None = None,
) -> bool:
    """Prompt for interactive confirmation.  Returns ``True`` if confirmed.

    Prints ``plan.render()`` first when given a plan. Returns ``False`` when
    the input ends, as it does where there is no terminal. Raises
    ``TypeError`` for an ``auto_yes`` that is not a bool.
    """
    if not isinstance(auto_yes, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"auto_yes must be True or False, not {auto_yes!r}")  # pyright: ignore[reportUnreachable]
    if plan is not None:
        print(plan.render())
    if auto_yes:
        return True
    try:
        response = input(f"{message} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return response in ("y", "yes")
