from typing import Literal

TunnelingProtocol = Literal["IPSEC", "GRE"]
TunnelMode = Literal["ACTIVE_ACTIVE", "ACTIVE_HOT_STANDBY", "ACTIVE_STANDBY"]
RoutingPolicy = Literal["POLICY", "ROUTE"]
ProviderCategory = Literal["DATACENTER"]
