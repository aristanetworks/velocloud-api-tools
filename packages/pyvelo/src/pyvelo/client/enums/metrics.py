from enum import StrEnum


class BasicMetricViewBy(StrEnum):
    APPLICATION = "application"
    EDGE_LOGICAL_ID = "edgeLogicalId"
    SOURCE_IP = "sourceIp"
    DEST_FQDN = "destFQDN"


class FlowFilterField(StrEnum):
    DEST_FQDN = "destFQDN"
    DEST_DOMAIN = "destDomain"
    SOURCE_IP = "sourceIp"
    DEST_IP = "destIp"
    GATEWAY_IP = "gatewayIp"
    HOST_NAME = "hostName"
    OS = "os"
    DEVICE_TYPE = "deviceType"
    SOURCE_MAC = "sourceMac"
    LINK_LOGICAL_ID = "linkLogicalId"
    FW_RULE_ID = "fwRuleId"
    APPLICATION = "application"
    DEST_PORT = "destPort"
    CATEGORY = "category"
    SEGMENT_ID = "segmentId"
    TRANSPORT = "transport"
    FLOW_PATH = "flowPath"
    LINK_ID = "linkId"
    FLOW_COUNT = "flowCount"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    RTT = "rtt"
    RETRANSMITS = "retransmits"
    EDGE_TO_EDGE_ENCRYPTION = "edgeToEdgeEncryption"
    ENTERPRISE_LOGICAL_ID = "enterpriseLogicalId"
    EDGE_LOGICAL_ID = "edgeLogicalId"
    DEST_LOGICAL_ID = "destLogicalId"
    BIZ_RULE_ID = "bizRuleId"


class FilterFieldOperator(StrEnum):
    IS = "is"
    IS_NOT = "isNot"
    IS_NULL = "isNull"
    IS_NOT_NULL = "isNotNull"
    STARTS_WITH = "startsWith"
    NOT_STARTS_WITH = "notStartsWith"
    ENDS_WITH = "endsWith"
    NOT_ENDS_WITH = "notEndsWith"
    CONTAINS = "contains"
    NOT_CONTAINS = "notContains"
    GREATER_OR_EQUALS = "greaterOrEquals"
    LESSER_OR_EQUALS = "lesserOrEquals"
    IN = "in"
    NOT_IN = "notIn"
    YES = "yes"
    NO = "no"
    CONTAINS_ADDRESS = "subnetContainsAddress"
    ADDRESS_TYPE = "addressType"
    BETWEEN = "between"


class EdgeTransportGroup(StrEnum):
    ALL = "ALL"
    PUBLIC = "PUBLIC"
    PUBLIC_WIRED = "PUBLIC_WIRED"
    PUBLIC_WIRELESS = "PUBLIC_WIRELESS"
    PRIVATE = "PRIVATE"
    PRIVATE_WIRED = "PRIVATE_WIRED"
    PRIVATE_WIRELESS = "PRIVATE_WIRELESS"
    UNKNOWN = "UNKNOWN"


class SdwanPeerPathMetric(StrEnum):
    SCORE_BEFORE_VIDEO = "scoreBeforeVideo"
    SCORE_BEFORE_TRANS = "scoreBeforeTrans"
    SCORE_AFTER_VOICE = "scoreAfterVoice"
    SCORE_AFTER_VIDEO = "scoreAfterVideo"
    SCORE_AFTER_TRANS = "scoreAfterTrans"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    ENCRYPTED_BYTES_RX = "encryptedBytesRx"
    ENCRYPTED_BYTES_TX = "encryptedBytesTx"
    ENCRYPTED_PACKETS_RX = "encryptedPacketsRx"
    ENCRYPTED_PACKETS_TX = "encryptedPacketsTx"
    UNENCRYPTED_BYTES_RX = "unencryptedBytesRx"
    UNENCRYPTED_BYTES_TX = "unencryptedBytesTx"
    UNENCRYPTED_PACKETS_RX = "unencryptedPacketsRx"
    UNENCRYPTED_PACKETS_TX = "unencryptedPacketsTx"
    BPS_RX = "bpsRx"
    BPS_TX = "bpsTx"
    PACKET_LOSS_RX = "packetLossRx"
    PACKET_LOSS_TX = "packetLossTx"
    LATENCY_RX = "latencyRx"
    LATENCY_TX = "latencyTx"
    JITTER_RX = "jitterRx"
    JITTER_TX = "jitterTx"


class PathSeriesViewBy(StrEnum):
    PATH = "path"
    PEER = "peer"


class PathStatus(StrEnum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"
    STANDBY = "STANDBY"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class DestMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class DestAttribute(StrEnum):
    DEST_DOMAIN = "destDomain"
    DEST_IP = "destIp"
    DEST_FQDN = "destFQDN"


class AppMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class DeviceMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class LinkMetric(StrEnum):
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    TOTAL_PACKETS = "totalPackets"
    P1_BYTES_RX = "p1BytesRx"
    P1_BYTES_TX = "p1BytesTx"
    P1_PACKETS_RX = "p1PacketsRx"
    P1_PACKETS_TX = "p1PacketsTx"
    P2_BYTES_RX = "p2BytesRx"
    P2_BYTES_TX = "p2BytesTx"
    P2_PACKETS_RX = "p2PacketsRx"
    P2_PACKETS_TX = "p2PacketsTx"
    P3_BYTES_RX = "p3BytesRx"
    P3_BYTES_TX = "p3BytesTx"
    P3_PACKETS_RX = "p3PacketsRx"
    P3_PACKETS_TX = "p3PacketsTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    CONTROL_BYTES_RX = "controlBytesRx"
    CONTROL_BYTES_TX = "controlBytesTx"
    CONTROL_PACKETS_RX = "controlPacketsRx"
    CONTROL_PACKETS_TX = "controlPacketsTx"
    P1_ENCRYPTED_BYTES_RX = "p1EncryptedBytesRx"
    P1_ENCRYPTED_BYTES_TX = "p1EncryptedBytesTx"
    P1_ENCRYPTED_PACKETS_RX = "p1EncryptedPacketsRx"
    P1_ENCRYPTED_PACKETS_TX = "p1EncryptedPacketsTx"
    P2_ENCRYPTED_BYTES_RX = "p2EncryptedBytesRx"
    P2_ENCRYPTED_BYTES_TX = "p2EncryptedBytesTx"
    P2_ENCRYPTED_PACKETS_RX = "p2EncryptedPacketsRx"
    P2_ENCRYPTED_PACKETS_TX = "p2EncryptedPacketsTx"
    P3_ENCRYPTED_BYTES_RX = "p3EncryptedBytesRx"
    P3_ENCRYPTED_BYTES_TX = "p3EncryptedBytesTx"
    P3_ENCRYPTED_PACKETS_RX = "p3EncryptedPacketsRx"
    P3_ENCRYPTED_PACKETS_TX = "p3EncryptedPacketsTx"
    CONTROL_ENCRYPTED_BYTES_RX = "controlEncryptedBytesRx"
    CONTROL_ENCRYPTED_BYTES_TX = "controlEncryptedBytesTx"
    CONTROL_ENCRYPTED_PACKETS_RX = "controlEncryptedPacketsRx"
    CONTROL_ENCRYPTED_PACKETS_TX = "controlEncryptedPacketsTx"
    ENCRYPTED_BYTES_RX = "encryptedBytesRx"
    ENCRYPTED_BYTES_TX = "encryptedBytesTx"
    ENCRYPTED_PACKETS_RX = "encryptedPacketsRx"
    ENCRYPTED_PACKETS_TX = "encryptedPacketsTx"
    UNENCRYPTED_BYTES_RX = "unencryptedBytesRx"
    UNENCRYPTED_BYTES_TX = "unencryptedBytesTx"
    UNENCRYPTED_PACKETS_RX = "unencryptedPacketsRx"
    UNENCRYPTED_PACKETS_TX = "unencryptedPacketsTx"
    BEST_BW_KBPS_RX = "bestBwKbpsRx"
    BEST_BW_KBPS_TX = "bestBwKbpsTx"
    BEST_JITTER_MS_RX = "bestJitterMsRx"
    BEST_JITTER_MS_TX = "bestJitterMsTx"
    BEST_LATENCY_MS_RX = "bestLatencyMsRx"
    BEST_LATENCY_MS_TX = "bestLatencyMsTx"
    BEST_LOSS_PCT_RX = "bestLossPctRx"
    BEST_LOSS_PCT_TX = "bestLossPctTx"
    BPS_OF_BEST_PATH_RX = "bpsOfBestPathRx"
    BPS_OF_BEST_PATH_TX = "bpsOfBestPathTx"
    SIGNAL_STRENGTH = "signalStrength"
    SCORE_TX = "scoreTx"
    SCORE_RX = "scoreRx"
    STATE = "state"
    AUTO_DUAL_MODE = "autoDualMode"
    MAX_INTERVAL_BPS_RX = "maxIntervalBpsRx"
    MAX_INTERVAL_BPS_TX = "maxIntervalBpsTx"
    MIN_INTERVAL_BPS_RX = "minIntervalBpsRx"
    MIN_INTERVAL_BPS_TX = "minIntervalBpsTx"


class AutoDualMode(StrEnum):
    SIM_IN_AS_ENABLE = "SIM_IN_AS_ENABLE"
    SIM_NOT_IN_AS_DISABLE = "SIM_NOT_IN_AS_DISABLE"
    SIM_IN_AS_DISABLE = "SIM_IN_AS_DISABLE"
    SIM_NOT_IN_AS_ENABLE = "SIM_NOT_IN_AS_ENABLE"
    NA = "N/A"
    EMPTY = ""


class FlowVisibilityMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    BEST_JITTER_MS_RX = "bestJitterMsRx"
    BEST_JITTER_MS_TX = "bestJitterMsTx"
    BEST_LATENCY_MS_RX = "bestLatencyMsRx"
    BEST_LATENCY_MS_TX = "bestLatencyMsTx"
    BEST_LOSS_PCT_RX = "bestLossPctRx"
    BEST_LOSS_PCT_TX = "bestLossPctTx"


class FlowVisibilitySortMetric(StrEnum):
    APPLICATION = "application"
    APP_CATEGORY = "appCategory"
    SOURCE_IP = "sourceIp"
    DEST_DOMAIN = "destDomain"
    DEST_FQDN = "destFqdn"
    DEST_IP = "destIp"
    SEGMENT_ID = "segmentId"
    DEST_PORT = "destPort"


class FlowVisibilityFilterField(StrEnum):
    SOURCE_IP = "sourceIp"
    SOURCE_PORT = "sourcePort"
    DEST_IP = "destIp"
    DEST_PORT = "destPort"
    SEGMENT_ID = "segmentId"
    HOST_NAME = "hostName"
    APPLICATION = "application"
    APP_CATEGORY = "appCategory"
    DEST_FQDN = "destFqdn"
    DEST_DOMAIN = "destDomain"
    NEXT_HOP = "nextHop"
    ROUTE = "route"
    TRANSPORT = "transport"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    LINK_LOGICAL_ID = "linkLogicalId"
    ISP = "isp"
    BUSINESS_RULE_NAME = "businessRuleName"
    FIREWALL_RULE_NAME = "firewallRuleName"


class FlowRoutePath(StrEnum):
    CLOUD_VIA_GATEWAY = "cloudViaGateway"
    INTERNET_DIRECT = "internetViaDirectBreakout"
    BRANCH_TO_BRANCH_VIA_GATEWAY = "branchToBranchViaGateway"
    BRANCH_TO_BRANCH_VIA_HUB = "branchToBranchViaHub"
    BRANCH_TO_BRANCH_DIRECT = "branchToBranchDirect"
    BRANCH_TO_NVS_DIRECT = "branchToNvsDirect"
    BRANCH_TO_NVS_VIA_GATEWAY = "branchToNvsViaGateway"
    BRANCH_TO_BACKHAUL = "branchToBackhaul"
    OTHER = "other"
    CLOUD_VIA_PARTNER_GATEWAY = "cloudViaPartnerGateway"
    BRANCH_ROUTED = "branchRouted"
    INTERNET_VIA_BRANCH_CSS = "internetViaBranchCSS"


class CategoryMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class SegmentMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class OsMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class NetworkInterfaceMetric(StrEnum):
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    DROPPED_RX = "droppedRx"
    DROPPED_TX = "droppedTx"
    ERRORS_RX = "errorsRx"
    ERRORS_TX = "errorsTx"
    AVG_PKT_SIZE_RX = "avgPktSizeRx"
    AVG_PKT_SIZE_TX = "avgPktSizeTx"
    TOTAL_BYTES = "totalBytes"
    TOTAL_PACKETS = "totalPackets"
    TOTAL_DROPPED = "totalDropped"
    TOTAL_ERRORS = "totalErrors"
    STATE = "state"


class AppLinkMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class AggregateEdgeLinkMetric(StrEnum):
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    TOTAL_PACKETS = "totalPackets"
    P1_BYTES_RX = "p1BytesRx"
    P1_BYTES_TX = "p1BytesTx"
    P1_PACKETS_RX = "p1PacketsRx"
    P1_PACKETS_TX = "p1PacketsTx"
    P2_BYTES_RX = "p2BytesRx"
    P2_BYTES_TX = "p2BytesTx"
    P2_PACKETS_RX = "p2PacketsRx"
    P2_PACKETS_TX = "p2PacketsTx"
    P3_BYTES_RX = "p3BytesRx"
    P3_BYTES_TX = "p3BytesTx"
    P3_PACKETS_RX = "p3PacketsRx"
    P3_PACKETS_TX = "p3PacketsTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    CONTROL_BYTES_RX = "controlBytesRx"
    CONTROL_BYTES_TX = "controlBytesTx"
    CONTROL_PACKETS_RX = "controlPacketsRx"
    CONTROL_PACKETS_TX = "controlPacketsTx"
    P1_ENCRYPTED_BYTES_RX = "p1EncryptedBytesRx"
    P1_ENCRYPTED_BYTES_TX = "p1EncryptedBytesTx"
    P1_ENCRYPTED_PACKETS_RX = "p1EncryptedPacketsRx"
    P1_ENCRYPTED_PACKETS_TX = "p1EncryptedPacketsTx"
    P2_ENCRYPTED_BYTES_RX = "p2EncryptedBytesRx"
    P2_ENCRYPTED_BYTES_TX = "p2EncryptedBytesTx"
    P2_ENCRYPTED_PACKETS_RX = "p2EncryptedPacketsRx"
    P2_ENCRYPTED_PACKETS_TX = "p2EncryptedPacketsTx"
    P3_ENCRYPTED_BYTES_RX = "p3EncryptedBytesRx"
    P3_ENCRYPTED_BYTES_TX = "p3EncryptedBytesTx"
    P3_ENCRYPTED_PACKETS_RX = "p3EncryptedPacketsRx"
    P3_ENCRYPTED_PACKETS_TX = "p3EncryptedPacketsTx"
    CONTROL_ENCRYPTED_BYTES_RX = "controlEncryptedBytesRx"
    CONTROL_ENCRYPTED_BYTES_TX = "controlEncryptedBytesTx"
    CONTROL_ENCRYPTED_PACKETS_RX = "controlEncryptedPacketsRx"
    CONTROL_ENCRYPTED_PACKETS_TX = "controlEncryptedPacketsTx"
    ENCRYPTED_BYTES_RX = "encryptedBytesRx"
    ENCRYPTED_BYTES_TX = "encryptedBytesTx"
    ENCRYPTED_PACKETS_RX = "encryptedPacketsRx"
    ENCRYPTED_PACKETS_TX = "encryptedPacketsTx"
    UNENCRYPTED_BYTES_RX = "unencryptedBytesRx"
    UNENCRYPTED_BYTES_TX = "unencryptedBytesTx"
    UNENCRYPTED_PACKETS_RX = "unencryptedPacketsRx"
    UNENCRYPTED_PACKETS_TX = "unencryptedPacketsTx"
    BEST_BW_KBPS_RX = "bestBwKbpsRx"
    BEST_BW_KBPS_TX = "bestBwKbpsTx"
    BEST_JITTER_MS_RX = "bestJitterMsRx"
    BEST_JITTER_MS_TX = "bestJitterMsTx"
    BEST_LATENCY_MS_RX = "bestLatencyMsRx"
    BEST_LATENCY_MS_TX = "bestLatencyMsTx"
    BEST_LOSS_PCT_RX = "bestLossPctRx"
    BEST_LOSS_PCT_TX = "bestLossPctTx"
    BPS_OF_BEST_PATH_RX = "bpsOfBestPathRx"
    BPS_OF_BEST_PATH_TX = "bpsOfBestPathTx"
    SIGNAL_STRENGTH = "signalStrength"
    SCORE_TX = "scoreTx"
    SCORE_RX = "scoreRx"
    STATE = "state"
    AUTO_DUAL_MODE = "autoDualMode"
    MAX_INTERVAL_BPS_RX = "maxIntervalBpsRx"
    MAX_INTERVAL_BPS_TX = "maxIntervalBpsTx"
    MIN_INTERVAL_BPS_RX = "minIntervalBpsRx"
    MIN_INTERVAL_BPS_TX = "minIntervalBpsTx"


class StatusMetric(StrEnum):
    TUNNEL_COUNT = "tunnelCount"
    TUNNEL_COUNT_V6 = "tunnelCountV6"
    MEMORY_PCT = "memoryPct"
    FLOW_COUNT = "flowCount"
    CPU_PCT = "cpuPct"
    CPU_CORE_TEMP = "cpuCoreTemp"
    HANDOFF_QUEUE_DROPS = "handoffQueueDrops"
    ROUTE_COUNT = "routeCount"
    ROUTE_COUNT_V6 = "routeCountV6"
    NAT_COUNT = "natCount"
    FLOW_UTIL_PCT = "flowUtilPct"
    TUNNEL_UTIL_PCT = "tunnelUtilPct"


class HealthStatsMetric(StrEnum):
    TUNNEL_COUNT = "tunnelCount"
    MEMORY_PCT = "memoryPct"
    FLOW_COUNT = "flowCount"
    CPU_PCT = "cpuPct"
    CPU_CORE_TEMP = "cpuCoreTemp"
    HANDOFF_QUEUE_DROPS = "handoffQueueDrops"
    TUNNEL_COUNT_V6 = "tunnelCountV6"
    ROUTE_COUNT = "routeCount"
    ROUTE_COUNT_V6 = "routeCountV6"
    NAT_COUNT = "natCount"
    FLOW_UTIL_PCT = "flowUtilPct"
    TUNNEL_UTIL_PCT = "tunnelUtilPct"


class GatewayStatusMetric(StrEnum):
    TUNNEL_COUNT = "tunnelCount"
    MEMORY_PCT = "memoryPct"
    FLOW_COUNT = "flowCount"
    CPU_PCT = "cpuPct"
    HANDOFF_QUEUE_DROPS = "handoffQueueDrops"
    CONNECTED_EDGES = "connectedEdges"
    TUNNEL_COUNT_V6 = "tunnelCountV6"


class FirewallIdpsMetric(StrEnum):
    THREATS_COUNT = "threatsCount"
    CRITICAL_THREATS_COUNT = "criticalThreatsCount"
    MAJOR_THREATS_COUNT = "majorThreatsCount"
    MINOR_THREATS_COUNT = "minorThreatsCount"
    INFORMATIONAL_THREATS_COUNT = "informationalThreatsCount"


class EnterpriseFirewallIdpsMetric(StrEnum):
    THREATS_COUNT = "threatsCount"
    CRITICAL_THREATS_COUNT = "criticalThreatsCount"
    MAJOR_THREATS_COUNT = "majorThreatsCount"
    MINOR_THREATS_COUNT = "minorThreatsCount"
    INFORMATIONAL_THREATS_COUNT = "informationalThreatsCount"
    EDGES_COUNT = "edgesCount"


class FirewallMaliciousIpMetric(StrEnum):
    THREATS_COUNT = "threatsCount"
    MONITORED_THREATS_COUNT = "monitoredThreatsCount"
    BLOCKED_THREATS_COUNT = "blockedThreatsCount"


class FirewallUrlCategoryMetric(StrEnum):
    THREATS_COUNT = "threatsCount"
    URLS_BLOCKED_COUNT = "urlsBlockedCount"
    URLS_MONITORED_COUNT = "urlsMonitoredCount"
    URLS_ALLOWED_COUNT = "urlsAllowedCount"


class FirewallUrlReputationMetric(StrEnum):
    THREATS_COUNT = "threatsCount"
    URLS_BLOCKED_COUNT = "urlsBlockedCount"
    URLS_MONITORED_COUNT = "urlsMonitoredCount"
    URLS_ALLOWED_COUNT = "urlsAllowedCount"
    URLS_HIGH_RISK_COUNT = "urlsHighRiskCount"
    URLS_MEDIUM_RISK_COUNT = "urlsMediumRiskCount"
    URLS_LOW_RISK_COUNT = "urlsLowRiskCount"
    URLS_TRUSTWORTHY_COUNT = "urlsTrustworthyCount"
    URLS_SUSPICIOUS_COUNT = "urlsSuspiciousCount"


class FirewallIdpsViewBy(StrEnum):
    """V1 portal API viewBy dimension for edge IDPS metrics."""

    SIGNATURE_NAME = "signatureName"
    THREAT_IMPACT_ALIAS = "threatImpactAlias"
    ALERT = "alert"
    THREAT_SOURCE_IP = "threatSourceIp"
    THREAT_TARGET_IP = "threatTargetIp"
    THREAT_SOURCE_GEO_COUNTRY = "threatSourceGeoCountry"


class EnterpriseFirewallIdpsViewBy(StrEnum):
    """V1 portal API viewBy dimension for enterprise IDPS metrics."""

    SIGNATURE_NAME = "signatureName"
    THREAT_IMPACT_ALIAS = "threatImpactAlias"
    ALERT = "alert"
    THREAT_SOURCE_IP = "threatSourceIp"
    THREAT_TARGET_IP = "threatTargetIp"
    THREAT_SOURCE_GEO_COUNTRY = "threatSourceGeoCountry"
    EDGE_LOGICAL_ID = "edgeLogicalId"


class FirewallIdpsSeriesViewBy(StrEnum):
    """V1 portal API viewBy dimension for IDPS series."""

    THREAT_IMPACT_ALIAS = "threatImpactAlias"


class FirewallMaliciousIpViewBy(StrEnum):
    """V1 portal API viewBy dimension for edge malicious IP metrics."""

    DEST_IP = "destIp"
    ACTION = "action"
    IP_CATEGORY = "ipCategory"
    SOURCE_IP = "sourceIp"
    THREAT_SOURCE_GEO_COUNTRY = "threatSourceGeoCountry"


class EnterpriseFirewallMaliciousIpViewBy(StrEnum):
    """V1 portal API viewBy dimension for enterprise malicious IP metrics."""

    DEST_IP = "destIp"
    ACTION = "action"
    IP_CATEGORY = "ipCategory"
    SOURCE_IP = "sourceIp"
    THREAT_SOURCE_GEO_COUNTRY = "threatSourceGeoCountry"
    EDGE_LOGICAL_ID = "edgeLogicalId"


class FirewallUrlCategoryViewBy(StrEnum):
    """V1 portal API viewBy dimension for edge URL category metrics."""

    URL_CATEGORY = "urlCategory"
    ACTION = "action"
    SOURCE_IP = "sourceIp"


class EnterpriseFirewallUrlCategoryViewBy(StrEnum):
    """V1 portal API viewBy dimension for enterprise URL category metrics."""

    URL_CATEGORY = "urlCategory"
    ACTION = "action"
    SOURCE_IP = "sourceIp"
    EDGE_LOGICAL_ID = "edgeLogicalId"


class FirewallUrlReputationViewBy(StrEnum):
    """V1 portal API viewBy dimension for edge URL reputation metrics."""

    DOMAIN_NAME = "domainName"
    SOURCE_IP = "sourceIp"
    URL_RISK = "urlRisk"
    ACTION = "action"


class EnterpriseFirewallUrlReputationViewBy(StrEnum):
    """V1 portal API viewBy dimension for enterprise URL reputation metrics."""

    DOMAIN_NAME = "domainName"
    SOURCE_IP = "sourceIp"
    URL_RISK = "urlRisk"
    ACTION = "action"
    EDGE_LOGICAL_ID = "edgeLogicalId"


class NsdTunnelStatsMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"
    FLOW_COUNT = "flowCount"


class EnterpriseFirewallEdgeSummaryMetric(StrEnum):
    IDPS_ACTIONS = "idpsActions"
    URL_CAT_ACTIONS = "urlCatActions"
    URL_REP_ACTIONS = "urlRepActions"
    MAL_IP_ACTIONS = "malIpActions"


class EnterpriseFirewallEdgeSummarySortMetric(StrEnum):
    TOTAL_ACTIONS = "totalActions"


class EnterpriseFirewallIdpsWith(StrEnum):
    SIGNATURE_ID = "signatureId"
    THREAT_IMPACT_ALIAS = "threatImpactAlias"
    SIGNATURE_CATEGORY = "signatureCategory"
    EDGE_LOGICAL_ID = "edgeLogicalId"


class EnterpriseFirewallMetricsWith(StrEnum):
    EDGE_LOGICAL_ID = "edgeLogicalId"


class NniMetric(StrEnum):
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    ERRORS_RX = "errorsRx"
    ERRORS_TX = "errorsTx"
    DROPS_RX = "dropsRx"
    DROPS_TX = "dropsTx"


__all__ = [
    "AggregateEdgeLinkMetric",
    "AppLinkMetric",
    "AppMetric",
    "BasicMetricViewBy",
    "CategoryMetric",
    "DestAttribute",
    "DestMetric",
    "DeviceMetric",
    "EdgeTransportGroup",
    "EnterpriseFirewallEdgeSummaryMetric",
    "EnterpriseFirewallEdgeSummarySortMetric",
    "EnterpriseFirewallIdpsMetric",
    "EnterpriseFirewallIdpsViewBy",
    "EnterpriseFirewallIdpsWith",
    "EnterpriseFirewallMaliciousIpViewBy",
    "EnterpriseFirewallMetricsWith",
    "EnterpriseFirewallUrlCategoryViewBy",
    "EnterpriseFirewallUrlReputationViewBy",
    "FilterFieldOperator",
    "FirewallIdpsMetric",
    "FirewallIdpsSeriesViewBy",
    "FirewallIdpsViewBy",
    "FirewallMaliciousIpMetric",
    "FirewallMaliciousIpViewBy",
    "FirewallUrlCategoryMetric",
    "FirewallUrlCategoryViewBy",
    "FirewallUrlReputationMetric",
    "FirewallUrlReputationViewBy",
    "FlowVisibilityFilterField",
    "FlowVisibilityMetric",
    "GatewayStatusMetric",
    "HealthStatsMetric",
    "LinkMetric",
    "NetworkInterfaceMetric",
    "NniMetric",
    "NsdTunnelStatsMetric",
    "OsMetric",
    "PathSeriesViewBy",
    "PathStatus",
    "SdwanPeerPathMetric",
    "SegmentMetric",
    "StatusMetric",
]
