"""Load VCO connection credentials from the environment."""

import logging
import os
from pathlib import Path

from pyvelo.client.exceptions import VcoCredentialsError

logger = logging.getLogger(__name__)


def get_vco_credentials(env_file: str | Path | None = None) -> tuple[str, str]:
    """Read ``VCO_HOST`` and ``VCO_TOKEN`` from the environment.

    If *env_file* is provided, variables are loaded from it first (without
    overriding values already present in the environment). Loading an env
    file requires the ``dotenv`` extra: ``pip install 'pyvelo[dotenv]'``.

    Raises:
        VcoCredentialsError: *env_file* does not exist, or either variable is
            missing or empty.
        ModuleNotFoundError: *env_file* is given but python-dotenv is not
            installed.
    """
    if env_file is not None:
        try:
            from dotenv import load_dotenv
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "Loading an env file requires python-dotenv; install it with: "
                + "pip install 'pyvelo[dotenv]'",
                name="dotenv",
            ) from exc
        path = Path(env_file)
        if not path.exists():
            raise VcoCredentialsError(f"Env file not found: {path}")
        _ = load_dotenv(path, override=False)
        logger.debug("Loaded env from %s", path)

    host = os.getenv("VCO_HOST")
    token = os.getenv("VCO_TOKEN")
    if not host or not token:
        raise VcoCredentialsError(
            "VCO_HOST and VCO_TOKEN environment variables are required"
        )
    return host, token
