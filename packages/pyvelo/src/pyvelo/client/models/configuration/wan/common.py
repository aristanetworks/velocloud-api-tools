from typing import Literal

WanMode = Literal["PUBLIC", "PRIVATE", "dynamic"]

LinkType = Literal["WIRED", "WIRELESS"]

NetworkType = Literal["ETHERNET", "WIRELESS", "WIRED"]

DiscoveryMode = Literal["STATIC", "DHCP", "AUTO", "USER_DEFINED", "AUTO_DISCOVERED"]

BandwidthMeasurement = Literal["USER_DEFINED", "AUTO", "MANUAL", "SLOW_START", "STATIC"]
