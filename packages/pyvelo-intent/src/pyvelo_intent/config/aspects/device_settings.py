"""Predefined device settings aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

DEVICE_SETTINGS_ASPECTS: list[Aspect] = [
    Aspect(
        name="dns",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="dns",
        description="DNS server configuration (per-segment)",
        tags=["dns", "networking", "name resolution"],
        segment_scoped=True,
    ),
    Aspect(
        name="vlans",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="lan.networks",
        description="VLAN / LAN network definitions",
        tags=["vlan", "lan", "network", "subnet"],
    ),
    Aspect(
        name="interfaces",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="routed_interfaces",
        description="Routed WAN/LAN interface configuration",
        tags=["interface", "wan", "port", "ge", "sfp"],
    ),
    Aspect(
        name="lan_interfaces",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="lan.interfaces",
        description="LAN port/wireless interface settings (access/trunk mode)",
        tags=["lan", "port", "switch", "wireless", "ssid"],
    ),
    Aspect(
        name="ntp",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="ntp",
        description="NTP time server settings",
        tags=["ntp", "time", "clock"],
    ),
    Aspect(
        name="syslog",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="syslog",
        description="Syslog forwarding configuration (per-segment)",
        tags=["syslog", "logging", "monitoring"],
        segment_scoped=True,
    ),
    Aspect(
        name="ha",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="ha",
        description="High availability settings",
        tags=["ha", "failover", "redundancy"],
    ),
    Aspect(
        name="vpn",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="vpn",
        description="VPN settings (hub, branch-to-branch, backhaul) per-segment",
        tags=["vpn", "hub", "branch", "backhaul", "tunnel"],
        segment_scoped=True,
    ),
    Aspect(
        name="nat",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="nat",
        description="LAN-side NAT rules (per-segment)",
        tags=["nat", "address translation", "port forwarding"],
        segment_scoped=True,
    ),
    Aspect(
        name="netflow",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="netflow",
        description="NetFlow/IPFIX collector configuration (per-segment)",
        tags=["netflow", "ipfix", "flow", "monitoring"],
        segment_scoped=True,
    ),
    Aspect(
        name="multicast",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="multicast",
        description="Multicast PIM/IGMP configuration (per-segment)",
        tags=["multicast", "pim", "igmp", "rp"],
        segment_scoped=True,
    ),
    Aspect(
        name="routes",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="routes",
        description="Static routes, ICMP probes, NSD routes (per-segment)",
        tags=["routes", "static", "icmp", "probe", "nsd"],
        segment_scoped=True,
    ),
    Aspect(
        name="snmp",
        module=ConfigurationModuleName.DEVICE_SETTINGS,
        path="snmp",
        description="SNMP v2c/v3 monitoring configuration",
        tags=["snmp", "monitoring", "community"],
    ),
]
