from enum import StrEnum


class BgpPeerState(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    CONNECT = "CONNECT"
    OPEN_SENT = "OPENSENT"
    OPEN_CONFIRM = "OPENCONFIRM"
    CLEARING = "CLEARING"
    IDLE_ADMIN = "IDLEADMIN"
    IDLE_PRFX_CT = "IDLEPRFXCT"
    REMOVED = "REMOVED"


class BfdPeerState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    INIT = "INIT"
    SHUTDOWN = "SHUTDOWN"
    UNKNOWN = "UNKNOWN"


class TunnelState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    STANDBY = "STANDBY"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class TunnelType(StrEnum):
    IPSEC = "IPSEC"
    GRE = "GRE"


class NvsTunnelTag(StrEnum):
    EDGE_NVS_TUNNEL = "EDGE_NVS_TUNNEL"
    NVS_FROM_EDGE_TUNNEL = "NVS_FROM_EDGE_TUNNEL"


class GatewayState(StrEnum):
    CONNECTED = "CONNECTED"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class DegradationViewBy(StrEnum):
    LINK = "link"
    EDGE = "edge"
    ISP = "isp"


class DegradationWith(StrEnum):
    BEFORE = "before"
    AFTER = "after"


class DegradationDetailsSortAttribute(StrEnum):
    LAST_UPDATED = "lastUpdated"
    ISP = "isp"
    WAN_LINK_TYPE = "wanLinkType"
    WAN_LINK_MODE = "wanLinkMode"
    TRAFFIC_TYPE = "trafficType"
    LINK_NAME = "linkName"
    EDGE_NAME = "edgeName"
    INTERFACE = "interface"
    LINK_IP_ADDRESS = "linkIpAddress"
    DEGRADATION_BEFORE_FAIR = "degradation.before.fairMinutes"
    DEGRADATION_BEFORE_CRITICAL = "degradation.before.criticalMinutes"
    DEGRADATION_AFTER_FAIR = "degradation.after.fairMinutes"
    DEGRADATION_AFTER_CRITICAL = "degradation.after.criticalMinutes"
    PACKET_LOSS_RX = "packetLossRx"
    JITTER_RX = "jitterRx"
    LATENCY_RX = "latencyRx"
    PACKET_LOSS_TX = "packetLossTx"
    JITTER_TX = "jitterTx"
    LATENCY_TX = "latencyTx"
    UPTIME_PCT = "uptimePct"
    SECONDS_UP = "secondsUp"
    SECONDS_DOWN = "secondsDown"
    SECONDS_UNKNOWN = "secondsUnknown"
    TOTAL_FLAPS = "totalFlaps"


class NvsProviderType(StrEnum):
    CLOUD_SECURITY_SERVICE = "cloudSecurityService"
    NVS_VIA_EDGE_SERVICE = "nvsViaEdgeService"


class GetAggregateEnterpriseEventsWith(StrEnum):
    EDGES = "edges"


class GetAggregateEdgeNvsTunnelStatusWith(StrEnum):
    DATA = "data"
    PROFILE_COUNT = "profileCount"
    EDGE_USAGE = "edgeUsage"
    CONFIGURATION = "configuration"
    GROUP_COUNT = "groupCount"
    EDGE_QOS_USAGE = "edgeQOSUsage"
    ENDPOINT_USAGE = "endpointUsage"


class GetReplicationStatusWith(StrEnum):
    CLIENT_CONTACT = "clientContact"
    STATE_HISTORY = "stateHistory"
    CLIENT_COUNT = "clientCount"
    STORAGE_INFO = "storageInfo"
