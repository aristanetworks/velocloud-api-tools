import uuid
from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.client.enums.network import (
    GetNetworkConfigurationsWith,
    GetNetworkEnterprisesWith,
    GetNetworkGatewayPoolsWith,
    GetNetworkGatewaysWith,
)
from pyvelo.client.schemas.edge import EdgeObject
from pyvelo.core import Alias, VcoModel, VcoTimestamp

# --- Existing schemas (getNetworkEnterprises) ---


class GetNetworkEnterprises(VcoModel):
    network_id: int | MISSING = MISSING
    enterprises: list[int] | MISSING = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    with_: Annotated[list[GetNetworkEnterprisesWith], Alias("with")] = Field(
        default_factory=list
    )


class GetNetworkEnterprisesResultItem(VcoModel):
    id_: Annotated[int, Alias("id")]
    name: str
    logical_id: uuid.UUID
    edge_count: int | MISSING = MISSING
    edges: list[EdgeObject] | MISSING = MISSING


# --- getApiTokens ---


class GetNetworkApiTokens(VcoModel):
    network_id: int


class GetNetworkApiTokensResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    expiration: VcoTimestamp | None | MISSING = MISSING
    user_id: int | None | MISSING = MISSING


# --- getEligibleReplacementGateways ---


class GetEligibleReplacementGateways(VcoModel):
    gateway_id: int
    service_states: list[str] | MISSING = MISSING
    roles: list[str] | MISSING = MISSING


class GetEligibleReplacementGatewaysResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    gateway_state: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING


# --- getNetworkCloneableEnterprises ---


class GetNetworkCloneableEnterprises(VcoModel):
    network_id: int


class GetNetworkCloneableEnterprisesResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING


# --- getNetworkConfigurations ---


class GetNetworkConfigurations(VcoModel):
    network_id: int
    with_: Annotated[list[GetNetworkConfigurationsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    id_: Annotated[int | MISSING, Alias("id")] = MISSING


class GetNetworkConfigurationsResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    version: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    refs: list[dict[str, Any]] | None | MISSING = MISSING


# --- getNetworkGatewayPools ---


class GetNetworkGatewayPools(VcoModel):
    network_id: int
    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    with_: Annotated[list[GetNetworkGatewayPoolsWith] | MISSING, Alias("with")] = (
        MISSING
    )


class GetNetworkGatewayPoolsResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    gateways: list[dict[str, Any]] | None | MISSING = MISSING
    enterprises: list[dict[str, Any]] | None | MISSING = MISSING


# --- getNetworkGateways ---


class GetNetworkGateways(VcoModel):
    network_id: int | MISSING = MISSING
    logical_ids: list[str] | MISSING = MISSING
    gateway_ids: list[int] | MISSING = MISSING
    with_: Annotated[list[GetNetworkGatewaysWith] | MISSING, Alias("with")] = MISSING


class GetNetworkGatewaysResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    logical_id: str | None | MISSING = MISSING
    gateway_state: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    software_version: str | None | MISSING = MISSING
    site: dict[str, Any] | None | MISSING = MISSING
    pools: list[dict[str, Any]] | None | MISSING = MISSING


# --- getNetworkOperatorUsers ---


class GetNetworkOperatorUsers(VcoModel):
    network_id: int


class GetNetworkOperatorUsersResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    role_name: str | None | MISSING = MISSING
    is_active: bool | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
