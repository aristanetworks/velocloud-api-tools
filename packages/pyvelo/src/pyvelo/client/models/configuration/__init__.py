from .common import SegmentMetadata
from .control_plane import ControlPlaneData
from .device_settings import (
    DeviceSegment,
    DeviceSettingsData,
    DeviceSettingsRefs,
    EdgeModel,
    IPv4AddressingType,
    IPv6AddressingType,
    LanInterface,
    LanSettings,
    RoutedInterface,
    RoutedInterfaceCell,
    RoutedInterfaceCommon,
    RoutedInterfaceGe,
    RoutedInterfaceLag,
    RoutedInterfaceSfp,
    RoutedInterfaceSfpDsl,
    RoutedInterfaceSfpGpon,
    RoutedInterfaceSfpStandard,
    RoutedInterfaceUsb,
    WanOverlay,
)
from .firewall import FirewallData, FirewallRefs
from .image_update import (
    FirmwareImageUpdateData,
    ImageUpdateData,
    ImageUpdateScheduledTime,
)
from .management_plane import (
    ManagementPlaneData,
    ManagementPlaneProxy,
    ManagementPlaneProxyDetail,
)
from .qos import QosData, QosRefs
from .wan import WanData, WanRefs

__all__ = [
    "ControlPlaneData",
    "DeviceSegment",
    "DeviceSettingsData",
    "DeviceSettingsRefs",
    "EdgeModel",
    "FirewallData",
    "FirewallRefs",
    "FirmwareImageUpdateData",
    "IPv4AddressingType",
    "IPv6AddressingType",
    "ImageUpdateData",
    "ImageUpdateScheduledTime",
    "LanInterface",
    "LanSettings",
    "ManagementPlaneData",
    "ManagementPlaneProxy",
    "ManagementPlaneProxyDetail",
    "QosData",
    "QosRefs",
    "RoutedInterface",
    "RoutedInterfaceCell",
    "RoutedInterfaceCommon",
    "RoutedInterfaceGe",
    "RoutedInterfaceLag",
    "RoutedInterfaceSfp",
    "RoutedInterfaceSfpDsl",
    "RoutedInterfaceSfpGpon",
    "RoutedInterfaceSfpStandard",
    "RoutedInterfaceUsb",
    "SegmentMetadata",
    "WanData",
    "WanOverlay",
    "WanRefs",
]
