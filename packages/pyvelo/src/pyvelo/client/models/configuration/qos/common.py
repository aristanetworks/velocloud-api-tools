from typing import Literal

# Routing types
RouteType = Literal["edge2Edge", "edge2Cloud", "edge2DataCenter", "edge2Any"]

RoutePolicy = Literal["auto", "direct", "gateway", "backhaul"]

# QoS Link and Service types
QosLinkPolicy = Literal[
    "auto", "fixed", "preferred", "preferredCorrect", "available", "required"
]

WANLinkServiceGroup = Literal[
    "ALL", "PUBLIC_WIRED", "PUBLIC_WIRELESS", "PRIVATE_WIRED", "PRIVATE_WIRELESS"
]

# Service class and priority
ServiceClass = Literal["realtime", "transactional", "bulk"]

FlowPriority = Literal["high", "normal", "low"]

# Network transport protocols
NetworkTransport = Literal["tcp", "udp", "icmp", "gre", "esp", "ah", "igmp"]

# Service rate limit input types
ServiceRateLimitInputType = Literal["percent", "rate"]

# CoS mapping input types
CosMappingInputType = Literal["weight", "bandwidth"]

RouteConfigType = Literal[
    "dataCenter", "cloudSecurityService", "edge", "cloudWebSecurityPolicy"
]

BackhaulEdgeType = Literal["edgeHub", "edgeHubCluster"]
