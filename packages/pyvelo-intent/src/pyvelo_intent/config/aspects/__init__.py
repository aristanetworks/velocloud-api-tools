"""Predefined aspect definitions for VCO configuration modules."""

from pyvelo_intent.config.aspect import AspectRegistry

from .control_plane import CONTROL_PLANE_ASPECTS
from .device_settings import DEVICE_SETTINGS_ASPECTS
from .firewall import FIREWALL_ASPECTS
from .qos import QOS_ASPECTS
from .wan import WAN_ASPECTS

ALL_ASPECTS = [
    *FIREWALL_ASPECTS,
    *DEVICE_SETTINGS_ASPECTS,
    *QOS_ASPECTS,
    *WAN_ASPECTS,
    *CONTROL_PLANE_ASPECTS,
]


def register_default_aspects(registry: AspectRegistry) -> None:
    """Register all predefined aspects into the given registry."""
    for aspect in ALL_ASPECTS:
        registry.register(aspect)


__all__ = [
    "ALL_ASPECTS",
    "CONTROL_PLANE_ASPECTS",
    "DEVICE_SETTINGS_ASPECTS",
    "FIREWALL_ASPECTS",
    "QOS_ASPECTS",
    "WAN_ASPECTS",
    "register_default_aspects",
]
