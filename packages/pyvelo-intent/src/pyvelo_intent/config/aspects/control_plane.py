"""Predefined control plane aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

CONTROL_PLANE_ASPECTS: list[Aspect] = [
    Aspect(
        name="gateway_selection",
        module=ConfigurationModuleName.CONTROL_PLANE,
        path="gateway_selection",
        description="Gateway selection configuration (primary/secondary)",
        tags=["gateway", "control plane", "vcg"],
    ),
]
