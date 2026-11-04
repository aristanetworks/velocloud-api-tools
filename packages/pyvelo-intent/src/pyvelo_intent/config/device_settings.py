"""Convenience re-exports for device settings configuration types.

Usage:
    from pyvelo_intent.config.device_settings import DeviceSettingsData, LanNetwork
"""

from pyvelo.client.models.configuration.device_settings.lan import (
    LanNetwork,
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
    Ntp,
)
from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterface,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentBgp,
    SegmentDns,
    SegmentOspf,
    SegmentVpn,
)

__all__ = [
    "DeviceSegment",
    "DeviceSettingsData",
    "LanNetwork",
    "LanSettings",
    "Ntp",
    "RoutedInterface",
    "SegmentBgp",
    "SegmentDns",
    "SegmentOspf",
    "SegmentVpn",
]
