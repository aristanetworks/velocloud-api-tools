import uuid
from typing import Annotated, Any

from pydantic import MISSING, Field

from pyvelo.client.enums.enterprise_proxy import (
    GetEnterpriseProxyEnterprisesWith,
    GetEnterpriseProxyGatewayPoolsWith,
    GetEnterpriseProxyGatewaysWith,
    GetEnterpriseProxyOperatorProfilesWith,
    GetEnterpriseProxyWith,
)
from pyvelo.core import (
    Alias,
    Tinyint,
    VcoModel,
    VcoTimestamp,
)

__all__ = [
    "EnterpriseProxyCapabilitiesResult",
    "EnterpriseProxyCloneableEnterpriseItem",
    "EnterpriseProxyConfigurationItem",
    "EnterpriseProxyEdgeInventoryItem",
    "EnterpriseProxyEdgeItem",
    "EnterpriseProxyEnterpriseItem",
    "EnterpriseProxyEnterprisesWithPropertyItem",
    "EnterpriseProxyGatewayItem",
    "EnterpriseProxyGatewayPoolItem",
    "EnterpriseProxyItem",
    "EnterpriseProxyOperatorProfileItem",
    "EnterpriseProxyPropertyResult",
    "EnterpriseProxyRateLimitsResult",
    "EnterpriseProxyResult",
    "EnterpriseProxyServiceItem",
    "EnterpriseProxyUserItem",
    "EnterpriseProxyUserResult",
    "GetEnterpriseProxies",
    "GetEnterpriseProxy",
    "GetEnterpriseProxyCapabilities",
    "GetEnterpriseProxyCloneableEnterprises",
    "GetEnterpriseProxyConfigurations",
    "GetEnterpriseProxyEdgeInventory",
    "GetEnterpriseProxyEdges",
    "GetEnterpriseProxyEnterprises",
    "GetEnterpriseProxyEnterprisesWithProperty",
    "GetEnterpriseProxyGatewayPools",
    "GetEnterpriseProxyGateways",
    "GetEnterpriseProxyOperatorProfiles",
    "GetEnterpriseProxyProperty",
    "GetEnterpriseProxyRateLimits",
    "GetEnterpriseProxyServices",
    "GetEnterpriseProxyUser",
    "GetEnterpriseProxyUsers",
]


# --- get_enterprise_proxy (enterpriseProxy/getEnterpriseProxy) ---


class GetEnterpriseProxy(VcoModel):

    id_: Annotated[
        int | MISSING,
        Alias("id"),
        Field(description="Partner/MSP integer ID; alias of enterpriseProxyId."),
    ] = MISSING
    enterprise_proxy_id: Annotated[
        int | MISSING,
        Field(
            description=(
                "Target partner/MSP integer ID. Inferred for an MSP session; "
                "otherwise identify the target partner explicitly."
            )
        ),
    ] = MISSING
    logical_id: Annotated[
        uuid.UUID | MISSING,
        Alias("enterpriseProxyLogicalId"),
        Field(
            description="Target partner/MSP logical UUID; alternative to an integer ID."
        ),
    ] = MISSING
    with_: Annotated[
        list[GetEnterpriseProxyWith] | MISSING,
        Alias("with"),
        Field(
            description=(
                "Related partner data to include: gatewayPools or gatewayPoolIds."
            )
        ),
    ] = MISSING


class EnterpriseProxyGatewayPoolItem(VcoModel):
    """A gateway pool optionally expanded by getEnterpriseProxy."""

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    gateways: list[dict[str, Any]] | None | MISSING = MISSING
    enterprises: list[dict[str, Any]] | None | MISSING = MISSING


class EnterpriseProxyResult(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    contact_name: str | None | MISSING = MISSING
    contact_phone: str | None | MISSING = MISSING
    contact_email: str | None | MISSING = MISSING
    street_address: str | None | MISSING = MISSING
    city: str | None | MISSING = MISSING
    state: str | None | MISSING = MISSING
    postal_code: str | None | MISSING = MISSING
    country: str | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    gateway_pools: list[EnterpriseProxyGatewayPoolItem] | None | MISSING = MISSING
    gateway_pool_ids: list[int] | None | MISSING = MISSING


# --- get_enterprise_proxies (enterpriseProxy/getEnterpriseProxies) ---


class GetEnterpriseProxies(VcoModel):
    pass


class EnterpriseProxyItem(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_proxy_edges (enterpriseProxy/getEnterpriseProxyEdges) ---


class GetEnterpriseProxyEdges(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyEdgeItem(VcoModel):

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    edge_state: str | None | MISSING = MISSING
    model_number: str | None | MISSING = MISSING
    software_version: str | None | MISSING = MISSING
    serial_number: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_name: str | None | MISSING = MISSING
    activation_state: str | None | MISSING = MISSING


# --- get_enterprise_proxy_enterprises (enterpriseProxy/getEnterpriseProxyEnterprises) ---


class GetEnterpriseProxyEnterprises(VcoModel):

    enterprise_proxy_id: Annotated[
        int | MISSING,
        Field(
            description=(
                "Target partner/MSP integer ID. Inferred for an MSP session and "
                "required for an operator selecting a partner."
            )
        ),
    ] = MISSING
    with_: Annotated[
        list[GetEnterpriseProxyEnterprisesWith] | MISSING,
        Alias("with"),
        Field(
            description=(
                "Related customer data to include: edges, edgeCount, "
                "edgeConfigUpdate, or serviceLicenses."
            )
        ),
    ] = MISSING


class EnterpriseProxyEnterpriseItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    account_number: str | None | MISSING = MISSING
    gateway_pool_id: int | None | MISSING = MISSING
    alerts_enabled: Tinyint | None | MISSING = MISSING
    operator_alerts_enabled: Tinyint | None | MISSING = MISSING
    edge_count: int | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    edges: list[dict[str, Any]] | None | MISSING = MISSING


# --- get_enterprise_proxy_gateway_pools (enterpriseProxy/getEnterpriseProxyGatewayPools) ---


class GetEnterpriseProxyGatewayPools(VcoModel):

    enterprise_proxy_id: int
    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    with_: Annotated[
        list[GetEnterpriseProxyGatewayPoolsWith] | MISSING, Alias("with")
    ] = MISSING


# --- get_enterprise_proxy_gateways (enterpriseProxy/getEnterpriseProxyGateways) ---


class GetEnterpriseProxyGateways(VcoModel):

    enterprise_proxy_id: int
    with_: Annotated[list[GetEnterpriseProxyGatewaysWith] | MISSING, Alias("with")] = (
        MISSING
    )


class EnterpriseProxyGatewayItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    logical_id: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    state: str | None | MISSING = MISSING
    gateway_state: str | None | MISSING = MISSING
    service_state: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_proxy_operator_profiles (enterpriseProxy/getEnterpriseProxyOperatorProfiles) ---


class GetEnterpriseProxyOperatorProfiles(VcoModel):

    enterprise_proxy_id: int
    with_: Annotated[
        list[GetEnterpriseProxyOperatorProfilesWith] | MISSING, Alias("with")
    ] = MISSING


class EnterpriseProxyOperatorProfileItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str
    description: str | None | MISSING = MISSING
    logical_id: uuid.UUID
    created: VcoTimestamp
    modified: VcoTimestamp
    version: str
    edges: list[Any] | MISSING = MISSING
    edge_count: int | MISSING = MISSING
    modules: list[dict[str, Any]] | MISSING = MISSING
    enterprises: list[Any] | MISSING = MISSING


# --- get_enterprise_proxy_property (enterpriseProxy/getEnterpriseProxyProperty) ---


class GetEnterpriseProxyProperty(VcoModel):

    enterprise_id: int | MISSING = MISSING
    name: str
    id_: Annotated[int | MISSING, Alias("id")] = MISSING


class EnterpriseProxyPropertyResult(VcoModel):

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    value: str | None | MISSING = MISSING
    data_type: str | None | MISSING = MISSING
    is_read_only: int | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_proxy_user (enterpriseProxy/getEnterpriseProxyUser) ---


class GetEnterpriseProxyUser(VcoModel):

    enterprise_proxy_id: int
    id_: Annotated[int, Alias("id")]
    username: str | MISSING = MISSING


class EnterpriseProxyUserResult(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    enterprise_proxy_id: int | None | MISSING = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    first_name: str | None | MISSING = MISSING
    last_name: str | None | MISSING = MISSING
    role_id: int | None | MISSING = MISSING
    role_name: str | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    is_locked: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_proxy_users (enterpriseProxy/getEnterpriseProxyUsers) ---


class GetEnterpriseProxyUsers(VcoModel):

    enterprise_proxy_id: int
    id_: Annotated[int | MISSING, Alias("id")] = MISSING


class EnterpriseProxyUserItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp | None | MISSING = MISSING
    enterprise_proxy_id: int | None | MISSING = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    first_name: str | None | MISSING = MISSING
    last_name: str | None | MISSING = MISSING
    role_id: int | None | MISSING = MISSING
    role_name: str | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    is_locked: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_proxy_configurations (enterpriseProxy/getEnterpriseProxyConfigurations) ---


class GetEnterpriseProxyConfigurations(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyConfigurationItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    version: str | None | MISSING = MISSING
    edge_count: int | None | MISSING = MISSING


# --- get_enterprise_proxy_edge_inventory (enterpriseProxy/getEnterpriseProxyEdgeInventory) ---


class GetEnterpriseProxyEdgeInventory(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyEdgeInventoryItem(VcoModel):

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    edges: list[dict[str, Any]] | None | MISSING = MISSING
    edge_count: int | None | MISSING = MISSING


# --- get_enterprise_proxy_rate_limits (enterpriseProxy/getEnterpriseProxyRateLimits) ---


class GetEnterpriseProxyRateLimits(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyRateLimitsResult(VcoModel):

    concurrency_limit: int | None | MISSING = MISSING
    rate_limit: int | None | MISSING = MISSING
    rate_limit_duration: int | None | MISSING = MISSING
    penalty_duration: int | None | MISSING = MISSING


# --- get_enterprise_proxy_services (enterpriseProxy/getEnterpriseProxyServices) ---


class GetEnterpriseProxyServices(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyServiceItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    type_: Annotated[str | None | MISSING, Alias("type")] = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING


# --- get_enterprise_proxy_capabilities (enterpriseProxy/getEnterpriseProxyCapabilities) ---


class GetEnterpriseProxyCapabilities(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyCapabilitiesResult(VcoModel):

    data: dict[str, Any] | None | MISSING = MISSING


# --- get_enterprise_proxy_cloneable_enterprises (enterpriseProxy/getEnterpriseProxyCloneableEnterprises) ---


class GetEnterpriseProxyCloneableEnterprises(VcoModel):

    enterprise_proxy_id: int


class EnterpriseProxyCloneableEnterpriseItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING


# --- get_enterprise_proxy_enterprises_with_property ---
# (enterpriseProxy/getEnterpriseProxyEnterprisesWithProperty)


class GetEnterpriseProxyEnterprisesWithProperty(VcoModel):

    enterprise_proxy_id: int
    name: str
    value: str
    enterprise_ids: list[int] | MISSING = MISSING


class EnterpriseProxyEnterprisesWithPropertyItem(VcoModel):

    id_: Annotated[int, Alias("id")]
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
