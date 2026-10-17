"""Helpers for constructing typed device-settings Pydantic objects from static specs."""

from typing import Any

from pyvelo.client.models.configuration.device_settings.interface_specs import (
    MODEL_INTERFACE_TEMPLATES,
)
from pyvelo.client.models.configuration.device_settings.root import ModelInterfaces
from pyvelo.core.base import ConfigContext


def synthesize_model_interfaces(model_class: str) -> ModelInterfaces | None:
    """Build a ``ModelInterfaces`` Pydantic object from static interface specs.

    Uses ``MODEL_INTERFACE_TEMPLATES`` to determine which interfaces exist
    for a given model class and whether each is ROUTED or SWITCHED, then
    constructs a typed ``ModelInterfaces`` instance.

    Returns ``None`` if the model class is not in the specs.
    """
    specs = MODEL_INTERFACE_TEMPLATES.get(model_class)
    if specs is None:
        return None

    routed_interfaces: list[dict[str, Any]] = []
    lan_interfaces: list[dict[str, Any]] = []

    for iface_name, spec in specs.items():
        if spec.mode == "ROUTED":
            routed_interfaces.append({"name": iface_name})
        else:
            lan_iface: dict[str, Any] = {
                "name": iface_name,
                "type": spec.type_,
                "vlanIds": [1],
            }
            if spec.ha_default:
                lan_iface["haDefault"] = True
            lan_interfaces.append(lan_iface)

    return ModelInterfaces.model_validate(
        {
            "routedInterfaces": routed_interfaces,
            "lan": {"interfaces": lan_interfaces},
        },
        context={"config_level": ConfigContext.PROFILE},
    )
