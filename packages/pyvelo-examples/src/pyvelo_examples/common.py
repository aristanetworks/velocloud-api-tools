"""Helpers shared by the standalone pyvelo examples."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def get_vco_credentials(env_file: str | Path | None = None) -> tuple[str, str]:
    """Read ``VCO_HOST`` and ``VCO_TOKEN`` from the environment.

    If *env_file* is provided, load it without overriding values already set
    in the process environment.
    """
    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise FileNotFoundError(f"Environment file not found: {path}")
        _ = load_dotenv(path, override=False)
        logger.debug("Loaded environment variables from %s", path)

    host = os.getenv("VCO_HOST")
    token = os.getenv("VCO_TOKEN")
    if not host or not token:
        raise ValueError("VCO_HOST and VCO_TOKEN environment variables are required")
    return host, token
