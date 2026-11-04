"""Predefined WAN aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

WAN_ASPECTS: list[Aspect] = [
    Aspect(
        name="wan_links",
        module=ConfigurationModuleName.WAN,
        path="links",
        description="WAN link configuration (MPLS, broadband, LTE)",
        tags=["wan", "links", "mpls", "broadband", "lte", "bandwidth"],
    ),
    Aspect(
        name="wan_networks",
        module=ConfigurationModuleName.WAN,
        path="networks",
        description="WAN network/overlay configuration",
        tags=["wan", "networks", "overlay", "underlay"],
    ),
]
