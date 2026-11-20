"""Small composable helpers for VCO configuration scripts."""

from pathlib import Path

import yaml
from pydantic import BaseModel


def load_config[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Load a YAML file and validate it against a Pydantic model."""
    with open(path) as f:
        return model.model_validate(yaml.safe_load(f))


def confirm_save(message: str = "Apply changes?", auto_yes: bool = False) -> bool:
    """Prompt for interactive confirmation.  Returns ``True`` if confirmed."""
    if auto_yes:
        return True
    response = input(f"{message} [y/N]: ").strip().lower()
    return response in ("y", "yes")
