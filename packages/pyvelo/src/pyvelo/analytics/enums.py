from enum import StrEnum


class FlowGroupBy(StrEnum):
    APP = "app"
    SOURCE = "sourceIp"
    CATEGORY = "category"
    DEST_IP = "destIp"
    DEST_FQDN = "destFqdn"
    DEST_DOMAIN = "destDomain"
    SEGMENT = "segment"
    APP_LINK = "appLink"


class FlowMetric(StrEnum):
    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"


class FirewallType(StrEnum):
    IDPS = "idps"
    MALICIOUS_IP = "maliciousIp"
    URL_CATEGORY = "urlCategory"
    URL_REPUTATION = "urlReputation"


class EnterpriseFirewallType(StrEnum):
    IDPS = "idps"
    EDGE_COUNT = "edgeCount"
    EDGE_SUMMARY = "edgeSummary"
    MALICIOUS_IP = "maliciousIp"
    URL_CATEGORY = "urlCategory"
    URL_REPUTATION = "urlReputation"
