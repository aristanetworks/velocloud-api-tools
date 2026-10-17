from enum import StrEnum


class GetEnterpriseProxyWith(StrEnum):
    GATEWAY_POOLS = "gatewayPools"
    GATEWAY_POOL_IDS = "gatewayPoolIds"


class GetEnterpriseProxyOperatorProfilesWith(StrEnum):
    EDGES = "edges"
    EDGE_COUNT = "edgeCount"
    MODULES = "modules"
    ENTERPRISES = "enterprises"


class GetEnterpriseProxyEnterprisesWith(StrEnum):
    EDGES = "edges"
    EDGE_COUNT = "edgeCount"
    EDGE_CONFIG_UPDATE = "edgeConfigUpdate"
    SERVICE_LICENSES = "serviceLicenses"


class GetEnterpriseProxyGatewayPoolsWith(StrEnum):
    GATEWAYS = "gateways"
    ENTERPRISES = "enterprises"
    GATEWAYS_WITH_ROLES = "gatewaysWithRoles"


class GetEnterpriseProxyGatewaysWith(StrEnum):
    SITE = "site"
    ROLES = "roles"
    POOLS = "pools"
    DATA_CENTERS = "dataCenters"
    CERTIFICATES = "certificates"
    ENTERPRISES = "enterprises"
    HAND_OFF_EDGES = "handOffEdges"
    ENTERPRISE_ASSOCIATIONS = "enterpriseAssociations"
    ENTERPRISE_ASSOCIATION_COUNTS = "enterpriseAssociationCounts"
    SYSLOG = "syslog"
