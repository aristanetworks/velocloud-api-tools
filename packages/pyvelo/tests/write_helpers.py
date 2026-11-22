"""Builders for the module write tests."""

import copy
from typing import Any

from pydantic import MISSING
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.testing import make_module_json


def fw(
    raw: dict[str, Any],
    *,
    module_id: int = 1,
    configuration_id: int = 10,
    version: str | None = "v1",
    refs: object = MISSING,
) -> FirewallConfigurationModule:
    """A firewall module read with *raw* as its data."""
    return FirewallConfigurationModule.model_validate(
        make_module_json(
            "firewall",
            copy.deepcopy(raw),
            module_id=module_id,
            configuration_id=configuration_id,
            version=version,
            refs=copy.deepcopy(refs),
        )
    )
