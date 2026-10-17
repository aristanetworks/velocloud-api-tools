from enum import StrEnum


class GetNetworkEnterprisesWith(StrEnum):
    EDGES = "edges"
    EDGE_COUNT = "edgeCount"


class GetNetworkConfigurationsWith(StrEnum):
    MODULES = "modules"
    COUNTS = "counts"


class GetNetworkGatewayPoolsWith(StrEnum):
    GATEWAYS = "gateways"
    ENTERPRISES = "enterprises"
    GATEWAYS_WITH_ROLES = "gatewaysWithRoles"
    CONNECTED_EDGE_LIST = "connectedEdgeList"


class GetNetworkGatewaysWith(StrEnum):
    SITE = "site"
    POOLS = "pools"
    ENTERPRISES = "enterprises"
    ENTERPRISE_ASSOCIATIONS = "enterpriseAssociations"
    ENTERPRISE_ASSOCIATION_COUNTS = "enterpriseAssociationCounts"
    DATA_CENTERS = "dataCenters"
    CERTIFICATES = "certificates"
    HAND_OFF_EDGES = "handOffEdges"
    ROLES = "roles"
    SYSLOG = "syslog"
    WSS = "wss"
