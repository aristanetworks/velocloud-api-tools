from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.client.enums.gateway import GetMigrationGatewaysWith
from pyvelo.core import Alias, VcoModel, VcoTimestamp
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, GatewayIdentity


class GetMigrationActions(VcoModel):
    enterprise_id: int | MISSING = MISSING
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")]
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    enterprise_object_id: int | MISSING = MISSING
    type_: Annotated[str | MISSING, Alias("type")] = MISSING


class MigrationActionResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    action_type: str | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


class GetMigrationGateways(VcoModel):
    enterprise_id: int | MISSING = MISSING
    gateway_id: Annotated[int | MISSING, Resolvable(GatewayIdentity, "id")] = MISSING
    with_: Annotated[list[GetMigrationGatewaysWith] | MISSING, Alias("with")] = MISSING


class MigrationGatewayResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    status: str | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


__all__ = [
    "GetMigrationActions",
    "GetMigrationGateways",
    "MigrationActionResult",
    "MigrationGatewayResult",
]
