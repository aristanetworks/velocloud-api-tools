"""Predefined control plane aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

CONTROL_PLANE_ASPECTS: list[Aspect] = [
    Aspect(
        name="bgp",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="bgp",
        description="BGP routing configuration (per-segment)",
        tags=["bgp", "routing", "as", "neighbor", "peering"],
        segment_scoped=True,
    ),
    Aspect(
        name="ospf",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="ospf",
        description="OSPF routing configuration (per-segment)",
        tags=["ospf", "routing", "area", "cost"],
        segment_scoped=True,
    ),
    Aspect(
        name="gateway_selection",
        module=ConfigurationModuleName.CONTROL_PLANE,
        path="gateway_selection",
        description="Gateway selection configuration (primary/secondary)",
        tags=["gateway", "control plane", "vcg"],
    ),
]
