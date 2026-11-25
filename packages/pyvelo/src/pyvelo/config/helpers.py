"""Small composable helpers for VCO configuration scripts."""

from collections.abc import Hashable
from pathlib import Path
from typing import cast, override

import yaml
from pydantic import BaseModel, ValidationError

from pyvelo.config.errors import ConfigInputError
from pyvelo.config.state import ConfigPlan


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a key given twice in one mapping.

    The keys are checked on the composed tree before anything is constructed,
    because constructing a mapping that merges another (``<<``) rewrites the
    merged mapping's own pairs. A key that overrides a merged one is legal.
    """

    @override
    def get_single_data(self) -> object:
        node = self.get_single_node()
        if node is None:
            return None
        self._refuse_repeated_keys(node, set())
        return cast("object", self.construct_document(node))  # pyright: ignore[reportUnknownMemberType]

    def _refuse_repeated_keys(self, node: yaml.Node, visited: set[int]) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))
        if isinstance(node, yaml.SequenceNode):
            for item in cast("list[yaml.Node]", node.value):
                self._refuse_repeated_keys(item, visited)
        elif isinstance(node, yaml.MappingNode):
            seen: set[object] = set()
            for key_node, value_node in cast(
                "list[tuple[yaml.Node, yaml.Node]]", node.value
            ):
                self._refuse_repeated_keys(value_node, visited)
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                self._refuse_repeated_keys(key_node, visited)
                key = cast("object", self.construct_object(key_node, deep=True))  # pyright: ignore[reportUnknownMemberType]
                if isinstance(key, Hashable) and key in seen:
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"the key {key!r} is given twice, keep one of them",
                        key_node.start_mark,
                    )
                seen.add(key)


def _location(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(top level)"


def load_config[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Load a YAML file and validate it against a Pydantic model.

    Raises ``ConfigInputError`` for a file that is not valid YAML, that gives
    a key twice in one mapping, or that the model refuses. The message names
    the file and each field at fault, not the values, which may be secrets.
    An ``OSError`` from opening the file is left as it is.
    """
    with Path(path).open() as f:
        try:
            data = cast("object", yaml.load(f, Loader=_UniqueKeyLoader))
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
    *,
    auto_yes: bool = False,
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
        print(plan.render())  # noqa: T201  # an interactive prompt: the person asked is shown the plan
    if auto_yes:
        return True
    try:
        response = input(f"{message} [y/N]: ").strip().lower()
    except (EOFError, RuntimeError):
        # The input ended, or the process has no stdin (``sys.stdin`` is None).
        return False
    return response in ("y", "yes")
