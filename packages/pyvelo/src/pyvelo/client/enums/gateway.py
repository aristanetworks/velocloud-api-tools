from enum import StrEnum


class GetMigrationGatewaysWith(StrEnum):
    SITE = "site"


class GatewayServiceState(StrEnum):
    IN_SERVICE = "IN_SERVICE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
