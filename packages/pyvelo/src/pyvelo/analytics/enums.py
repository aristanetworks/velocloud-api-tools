"""The flow groupings, flow metrics and firewall statistics ``Analytics`` takes."""

from enum import StrEnum


class FlowGroupBy(StrEnum):
    """The grouping of an edge's flow statistics, which picks the VCO method.

    ``APP``, ``SOURCE``, ``CATEGORY`` and ``SEGMENT`` go to the edge app, device,
    category and segment methods; the three ``DEST_`` values go to the dest
    methods with that destination attribute; ``APP_LINK`` to the app-link methods.
    """

    APP = "app"
    SOURCE = "sourceIp"
    CATEGORY = "category"
    DEST_IP = "destIp"
    DEST_FQDN = "destFqdn"
    DEST_DOMAIN = "destDomain"
    SEGMENT = "segment"
    APP_LINK = "appLink"


class FlowMetric(StrEnum):
    """A flow metric, as the metric enum of the method ``FlowGroupBy`` picks."""

    PACKETS_RX = "packetsRx"
    PACKETS_TX = "packetsTx"
    TOTAL_PACKETS = "totalPackets"
    BYTES_RX = "bytesRx"
    BYTES_TX = "bytesTx"
    TOTAL_BYTES = "totalBytes"


class FirewallType(StrEnum):
    """The edge firewall statistics ``get_edge_firewall_metrics`` reads."""

    IDPS = "idps"
    MALICIOUS_IP = "maliciousIp"
    URL_CATEGORY = "urlCategory"
    URL_REPUTATION = "urlReputation"


class EnterpriseFirewallType(StrEnum):
    """The enterprise firewall statistics ``get_enterprise_firewall_metrics`` reads.

    ``EDGE_COUNT`` gives the total and impacted edge counts as one row;
    ``EDGE_SUMMARY`` gives a row per edge.
    """

    IDPS = "idps"
    EDGE_COUNT = "edgeCount"
    EDGE_SUMMARY = "edgeSummary"
    MALICIOUS_IP = "maliciousIp"
    URL_CATEGORY = "urlCategory"
    URL_REPUTATION = "urlReputation"
