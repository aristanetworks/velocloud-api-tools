"""Small composable helpers for VCO configuration scripts."""

import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from pyvelo_intent.errors import ConfigInputError, MissingEnvVarError

logger = logging.getLogger(__name__)


def get_vco_credentials(env_file: str | Path | None = None) -> tuple[str, str]:
    """Read ``VCO_HOST`` and ``VCO_TOKEN`` from the environment.

    If *env_file* is provided, variables are loaded from it first (without
    overriding values already present in the environment).
    """
    if env_file is not None:
        path = Path(env_file)
        if not path.exists():
            raise ConfigInputError(f"Env file not found: {path}")
        _ = load_dotenv(path, override=False)
        logger.debug("Loaded env from %s", path)

    host = os.getenv("VCO_HOST")
    token = os.getenv("VCO_TOKEN")
    if not host or not token:
        raise MissingEnvVarError(
            "VCO_HOST and VCO_TOKEN environment variables are required"
        )
    return host, token


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
