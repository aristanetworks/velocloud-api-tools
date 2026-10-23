import uuid
from typing import Annotated, Any, Literal

from annotated_types import Ge, Le
from pydantic import MISSING, ConfigDict, Discriminator, Field, model_validator

from pyvelo.client.enums.edge import (
    EdgeField,
    GetEdgesWith,
    GetEnterpriseEdgeListWith,
)
from pyvelo.client.enums.enterprise import (
    BgpFilterActionType,
    BgpFilterMatchType,
    BgpFilterPermitDeny,
    CustomAppStatus,
    EnterpriseCustomAppsParamsWith,
    EnterpriseNetworkSegmentsParamsWith,
    EnterpriseServicesParamsWith,
    EnterpriseServiceType,
    GetEnterpriseNetworkAllocationWith,
    GetEnterpriseOperatorConfigurationWith,
    GetEnterpriseSecurityObjectsWith,
    GetEnterpriseWith,
    NetworkSegmentType,
    ObjectGroupType,
    SecurityObjectType,
)
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.models.enterprise import (
    AddressGroupEntry,
    AuthenticationServiceData,
    CloudSecurityServiceData,
    CustomAppData,
    DataCenterServiceData,
    DnsServiceData,
    EdgeHubClusterMemberServiceData,
    EdgeHubClusterServiceData,
    EdgeHubServiceData,
    Gateway,
    LocalUiCredentialsData,
    NetflowCollectorData,
    NetflowFilterData,
    NetworkSegmentData,
    NvsViaEdgeServiceData,
    PortGroupEntry,
    PrefixTagData,
    ServiceConfiguration,
    ServiceEdgeUsage,
    ServiceQosUsage,
    SymantecWssLocationUsage,
    WssIntegrationData,
)
from pyvelo.client.models.enterprise.service_data import IaasSubscriptionData
from pyvelo.client.schemas.configuration import ApplicationItem, Configuration
from pyvelo.client.schemas.edge import (
    EdgeCertificate,
    EdgeHaInfo,
    EdgeLicense,
    EdgeLink,
    EdgeObject,
    Site,
)
from pyvelo.client.schemas.edge_filters import EdgeGroupFilters
from pyvelo.client.schemas.enterprise_proxy import EnterpriseProxyResult
from pyvelo.core import (
    Alias,
    NoneIsMissing,
    PaginatedVcoModel,
    Tinyint,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import (
    EdgeIdentity,
    GatewayIdentity,
    ProfileIdentity,
    SegmentIdentity,
)


class GetEnterpriseEdges(PaginatedVcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    with_: Annotated[list[GetEdgesWith] | MISSING, Alias("with")] = MISSING
    edge_ids: list[int] | MISSING = MISSING
    prune_data: bool | MISSING = MISSING
    limit: int = 200
    next_page_link: str | MISSING = MISSING
    prev_page_link: str | MISSING = MISSING
    sort_by: Any | MISSING = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    quick_search: Any | MISSING = MISSING
    fields_needed: list[EdgeField] | MISSING = MISSING
    filters: EdgeGroupFilters | MISSING = MISSING
    filter_spec: Annotated[bool, Alias("_filterSpec")] = True


class GetEnterpriseEdgesItem(EdgeObject):
    certificates: list[EdgeCertificate] | None | MISSING = MISSING
    certificate_summary: dict[str, Any] | None | MISSING = MISSING
    configuration: Configuration | None | MISSING = MISSING
    ha: EdgeHaInfo | None | MISSING = MISSING
    licenses: list[EdgeLicense] | None | MISSING = MISSING
    links: list[EdgeLink] | None | MISSING = MISSING
    recent_links: list[EdgeLink] | None | MISSING = MISSING
    site: Site | None | MISSING = MISSING
    is_hub: bool | None | MISSING = MISSING
    is_software_version_supported_by_vco: bool | None | MISSING = MISSING
    secure_device_secrets: bool | None | MISSING = MISSING
    cloud_services: list[dict[str, Any]] | None | MISSING = MISSING
    nvs_from_edge: list[dict[str, Any]] | None | MISSING = MISSING


class GetEnterpriseEdgeList(PaginatedVcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = Field(
        default=MISSING, ge=1
    )
    with_: Annotated[list[GetEnterpriseEdgeListWith] | MISSING, Alias("with")] = MISSING
    edge_ids: list[int] | MISSING = MISSING
    only_hub_edges: bool | MISSING = MISSING
    limit: int = 200
    next_page_link: str | MISSING = MISSING
    prev_page_link: str | MISSING = MISSING
    sort_by: Any | MISSING = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    quick_search: Any | MISSING = MISSING
    filters: EdgeGroupFilters | MISSING = MISSING
    filter_spec: Annotated[bool, Alias("_filterSpec")] = True


class GetEnterpriseEdgeListItem(GetEnterpriseEdgesItem):
    is_assigned_gateway_quiesced: bool | None | MISSING = MISSING
    wan_links: WanData | None | MISSING = MISSING


class PrivateNetworkData(VcoModel):
    private_network_id: int


class GetEnterpriseServices(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[EnterpriseServicesParamsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    type_: Annotated[EnterpriseServiceType | MISSING, Alias("type")] = MISSING
    name: str | MISSING = MISSING
    logical_id: uuid.UUID | list[uuid.UUID] | MISSING = MISSING
    include_partner_gateways: bool | MISSING = MISSING


class InsertEnterpriseService(VcoModel):
    """Request to create a new enterprise service."""

    type_: Annotated[str, Alias("type")]
    name: str
    data: dict[str, Any]
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


class DeleteEnterpriseService(VcoModel):
    """Request to delete an enterprise service."""

    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    logical_id: str | MISSING = MISSING
    enterprise_id: int | MISSING = MISSING


class EnterpriseServiceBase(VcoModel):
    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING
    logical_id: uuid.UUID
    name: str
    created: VcoTimestamp
    version: str | MISSING = MISSING

    alerts_enabled: Tinyint | MISSING = MISSING
    operator_alerts_enabled: Tinyint | MISSING = MISSING
    description: str | None | MISSING = MISSING
    draft_comment: str | None | MISSING = MISSING
    draft_created: VcoTimestamp | None | MISSING = MISSING
    draft_data: str | None | MISSING = MISSING
    edge_count: int | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_name: str | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    group_count: int | MISSING = MISSING
    parent_group_id: int | None | MISSING = MISSING
    last_contact: VcoTimestamp | None | MISSING = MISSING

    profile_count: int | MISSING = MISSING
    gateways: list[Gateway] | MISSING = MISSING
    configuration: list[ServiceConfiguration] | MISSING = MISSING
    edge_qos_usage: list[ServiceQosUsage] | MISSING = MISSING
    edge_usage: list[ServiceEdgeUsage] | MISSING = MISSING
    wss_usage: list[SymantecWssLocationUsage] | SymantecWssLocationUsage | MISSING = (
        MISSING
    )


class AuthenticationService(EnterpriseServiceBase):
    type_: Annotated[Literal["authentication"], Alias("type")] = "authentication"
    data: AuthenticationServiceData


class DnsService(EnterpriseServiceBase):
    type_: Annotated[Literal["dns"], Alias("type")] = "dns"
    data: DnsServiceData


class DataCenterService(EnterpriseServiceBase):
    type_: Annotated[Literal["dataCenter"], Alias("type")] = "dataCenter"
    data: DataCenterServiceData


class IaasSubscriptionService(EnterpriseServiceBase):
    type_: Annotated[Literal["iaasSubscription"], Alias("type")] = "iaasSubscription"
    data: IaasSubscriptionData


class NetflowCollectorService(EnterpriseServiceBase):
    type_: Annotated[Literal["netflowCollector"], Alias("type")] = "netflowCollector"
    data: NetflowCollectorData


class NetflowFilterService(EnterpriseServiceBase):
    type_: Annotated[Literal["netflowFilter"], Alias("type")] = "netflowFilter"
    data: NetflowFilterData


class LocalUiCredentialsService(EnterpriseServiceBase):
    type_: Annotated[Literal["localUiCredentials"], Alias("type")] = (
        "localUiCredentials"
    )
    data: LocalUiCredentialsData


class EdgeHubService(EnterpriseServiceBase):
    type_: Annotated[Literal["edgeHub"], Alias("type")] = "edgeHub"
    data: EdgeHubServiceData


class EdgeHubClusterService(EnterpriseServiceBase):
    type_: Annotated[Literal["edgeHubCluster"], Alias("type")] = "edgeHubCluster"
    data: EdgeHubClusterServiceData


class EdgeHubClusterMemberService(EnterpriseServiceBase):
    type_: Annotated[Literal["edgeHubClusterMember"], Alias("type")] = (
        "edgeHubClusterMember"
    )
    data: EdgeHubClusterMemberServiceData


class CloudSecurityService(EnterpriseServiceBase):
    type_: Annotated[Literal["cloudSecurityService"], Alias("type")] = (
        "cloudSecurityService"
    )
    data: CloudSecurityServiceData


class NvsViaEdgeService(EnterpriseServiceBase):
    type_: Annotated[Literal["nvsViaEdgeService"], Alias("type")] = "nvsViaEdgeService"
    data: NvsViaEdgeServiceData


class PrefixTagService(EnterpriseServiceBase):
    type_: Annotated[Literal["prefixTag"], Alias("type")] = "prefixTag"
    data: PrefixTagData


class WssIntegrationService(EnterpriseServiceBase):
    type_: Annotated[Literal["wssIntegration"], Alias("type")] = "wssIntegration"
    data: WssIntegrationData


class GenericService(EnterpriseServiceBase):
    type_: Annotated[
        Literal[
            "edgeBranchCluster",
            "edgeBranchClusterMember",
            "securityVnf",
            "securityVnfService",
            "securityVnfLicense",
            "vnfImage",
            "cloudWebSecurity",
            "cloudWebSecurityPolicy",
            "logExportEndpoint",
            "edgeToEdgeBridge",
            "backHaulEdge",
            "cloudToCloudInterConnect",
            "secureAccess",
        ],
        Alias("type"),
    ]
    data: dict[str, Any]


type AnyEnterpriseService = Annotated[
    AuthenticationService
    | DnsService
    | DataCenterService
    | NetflowCollectorService
    | NetflowFilterService
    | LocalUiCredentialsService
    | IaasSubscriptionService
    | EdgeHubService
    | EdgeHubClusterService
    | EdgeHubClusterMemberService
    | CloudSecurityService
    | NvsViaEdgeService
    | PrefixTagService
    | WssIntegrationService
    | GenericService,
    Discriminator(discriminator="type_"),
]


class GetEnterprisePrivateNetworks(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[EnterpriseServicesParamsWith] | MISSING, Alias("with")] = (
        MISSING
    )


class GetNsdConfiguration(VcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING


class BgpFilterMatchConfig(VcoModel):
    type_: Annotated[BgpFilterMatchType, Alias("type")]
    value: str
    exact_match: bool | MISSING = MISSING


class BgpFilterActionValue(VcoModel):
    type_: Annotated[BgpFilterActionType, Alias("type")]
    value: str


class BgpFilterActionConfig(VcoModel):
    type_: Annotated[BgpFilterPermitDeny, Alias("type")]
    values: list[BgpFilterActionValue]
    community_additive: bool | MISSING = MISSING


class BgpFilterRule(VcoModel):
    match: BgpFilterMatchConfig
    action: BgpFilterActionConfig


class NsdBgpFilter(VcoModel):
    id_: Annotated[str, Alias("id")]
    name: str
    is_locked_by_profile: bool | MISSING = MISSING
    rules: list[BgpFilterRule]
    version: str | MISSING = MISSING


class NsdGatewayConfig(VcoModel):
    local_asn: Annotated[str | None | MISSING, Alias("localASN")] = MISSING
    router_id: str | None | MISSING = MISSING
    version: str | MISSING = MISSING


class DatacenterNsdConfig(VcoModel):
    filters: list[NsdBgpFilter]
    gateways: dict[str, NsdGatewayConfig]


class GetEnterpriseNsdConfigurationResult(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    operator_id: int | None
    network_id: int | None
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | None, Resolvable(EdgeIdentity, "id")]
    gateway_id: Annotated[int | None, Resolvable(GatewayIdentity, "id")]
    parent_group_id: int | None
    description: str | None
    object_: Annotated[Literal["NSD_CONFIGURATION"], Alias("object")] = (
        "NSD_CONFIGURATION"
    )
    name: str
    type_: Annotated[Literal["DEFAULT"] | str, Alias("type")]
    logical_id: uuid.UUID
    alerts_enabled: Tinyint
    operator_alerts_enabled: Tinyint
    status: str | None
    status_modified: VcoTimestamp
    previous_data: str | None
    previous_created: VcoTimestamp
    draft_data: str | None
    draft_created: VcoTimestamp
    draft_comment: str | None
    data: dict[str, DatacenterNsdConfig]
    last_contact: VcoTimestamp
    version: str
    modified: VcoTimestamp


class PrivateNetwork(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    enterprise_id: int | MISSING = MISSING
    name: str
    logical_id: uuid.UUID
    alerts_enabled: Tinyint
    operator_alerts_enabled: Tinyint
    object_: Annotated[Literal["PRIVATE_NETWORK"], Alias("object")] = "PRIVATE_NETWORK"
    type_: Annotated[str, Alias("type")]
    version: str
    modified: VcoTimestamp
    data: PrivateNetworkData

    operator_id: int | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    parent_group_id: int | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    status_modified: VcoTimestamp | MISSING = MISSING
    previous_data: str | None | MISSING = MISSING
    previous_created: VcoTimestamp | MISSING = MISSING
    draft_data: str | None | MISSING = MISSING
    draft_created: VcoTimestamp | MISSING = MISSING
    draft_comment: str | None | MISSING = MISSING
    last_contact: VcoTimestamp | MISSING = MISSING

    profile_count: int | MISSING = MISSING
    edge_count: int | MISSING = MISSING
    edge_usage: list[ServiceEdgeUsage] | MISSING = MISSING
    configuration: list[ServiceConfiguration] | MISSING = MISSING


class GetObjectGroups(VcoModel):
    enterprise_id: int | MISSING = MISSING
    type_: Annotated[ObjectGroupType, Alias("type")]
    id_: Annotated[int | MISSING, Alias("id")] = MISSING


class InsertObjectGroup(VcoModel):
    """Request to create a new object group (address group or port group)."""

    name: str
    type_: Annotated[ObjectGroupType | MISSING, Alias("type")] = MISSING
    data: list[dict[str, Any]]
    enterprise_id: int | MISSING = MISSING
    description: str | MISSING = MISSING


class UpdateObjectGroup(VcoModel):
    """Request to update an existing object group."""

    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING
    name: str | MISSING = MISSING
    description: str | MISSING = MISSING
    data: list[dict[str, Any]] | MISSING = MISSING


class DeleteObjectGroup(VcoModel):
    """Request to delete an object group."""

    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING


class ObjectGroupBase(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    enterprise_id: int | MISSING = MISSING
    name: str
    logical_id: uuid.UUID
    alerts_enabled: Tinyint
    operator_alerts_enabled: Tinyint
    object_: Annotated[Literal["PROPERTY"], Alias("object")] = "PROPERTY"
    version: str
    modified: VcoTimestamp

    operator_id: int | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    parent_group_id: int | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    status_modified: VcoTimestamp | MISSING = MISSING
    previous_data: str | None | MISSING = MISSING
    previous_created: VcoTimestamp | MISSING = MISSING
    draft_data: str | None | MISSING = MISSING
    draft_created: VcoTimestamp | MISSING = MISSING
    draft_comment: str | None | MISSING = MISSING
    last_contact: VcoTimestamp | MISSING = MISSING


class AddressObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["address_group"], Alias("type")] = "address_group"
    data: list[AddressGroupEntry]


class PortObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["port_group"], Alias("type")] = "port_group"
    data: list[PortGroupEntry]


class IdpsConfiguration(VcoModel):
    ids_enabled: bool
    ips_enabled: bool
    log_enabled: bool


class IdpsObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["idps"], Alias("type")] = "idps"
    data: IdpsConfiguration


class UrlCategoryFilteringConfiguration(VcoModel):
    allow_and_log_categories: list[int]
    blocked_categories: list[int]
    unknown_category_action: Literal["allow", "block"]


class UrlCategoryFilteringObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["urlCategoryFiltering"], Alias("type")] = (
        "urlCategoryFiltering"
    )
    data: UrlCategoryFilteringConfiguration


class UrlReputationFilteringConfiguration(VcoModel):
    min_reputation_score: int
    allow_and_log_reputations: list[int]
    unknown_reputation_action: Literal["allow", "block"]


class UrlReputationFilteringObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["urlReputationFiltering"], Alias("type")] = (
        "urlReputationFiltering"
    )
    data: UrlReputationFilteringConfiguration


class MaliciousIpFilteringConfiguration(VcoModel):
    action: Literal["allowAndLog", "block"]


class MaliciousIpFilteringObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["maliciousIpFiltering"], Alias("type")] = (
        "maliciousIpFiltering"
    )
    data: MaliciousIpFilteringConfiguration


class SecurityServiceGroupRef(VcoModel):
    id: int
    logical_id: uuid.UUID | MISSING = MISSING


class SecurityServiceGroupConfiguration(VcoModel):
    idps: SecurityServiceGroupRef | MISSING = MISSING
    url_category_filtering: SecurityServiceGroupRef | MISSING = MISSING
    url_reputation_filtering: SecurityServiceGroupRef | MISSING = MISSING
    malicious_ip_filtering: SecurityServiceGroupRef | MISSING = MISSING


class SecurityServiceGroupObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["securityServiceGroup"], Alias("type")] = (
        "securityServiceGroup"
    )
    data: SecurityServiceGroupConfiguration


class CustomAppObjectGroup(ObjectGroupBase):
    type_: Annotated[Literal["customApp"], Alias("type")] = "customApp"
    data: ApplicationItem


type AnyObjectGroup = Annotated[
    AddressObjectGroup
    | PortObjectGroup
    | IdpsObjectGroup
    | UrlCategoryFilteringObjectGroup
    | UrlReputationFilteringObjectGroup
    | MaliciousIpFilteringObjectGroup
    | SecurityServiceGroupObjectGroup
    | CustomAppObjectGroup,
    Discriminator(discriminator="type_"),
]


class GetEnterpriseNetworkSegments(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[
        list[EnterpriseNetworkSegmentsParamsWith] | MISSING, Alias("with")
    ] = MISSING


class InsertEnterpriseNetworkSegment(VcoModel):
    """Request to create a new network segment."""

    name: str
    type_: Annotated[NetworkSegmentType, Alias("type")]
    data: NetworkSegmentData
    enterprise_id: int | MISSING = MISSING
    description: str | None | MISSING = MISSING


class DeleteEnterpriseNetworkSegment(VcoModel):
    """Request to delete a network segment."""

    id_: Annotated[int, Alias("id")]
    enterprise_id: int | MISSING = MISSING


class NetworkSegment(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    enterprise_id: int | MISSING = MISSING
    name: str
    logical_id: uuid.UUID
    alerts_enabled: Tinyint
    operator_alerts_enabled: Tinyint
    object_: Annotated[Literal["NETWORK_SEGMENT"], Alias("object")]
    type_: Annotated[NetworkSegmentType, Alias("type")]
    version: str
    modified: VcoTimestamp
    data: NetworkSegmentData

    operator_id: int | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    parent_group_id: int | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    status_modified: VcoTimestamp | MISSING = MISSING
    previous_data: str | None | MISSING = MISSING
    previous_created: VcoTimestamp | MISSING = MISSING
    draft_data: str | None | MISSING = MISSING
    draft_created: VcoTimestamp | MISSING = MISSING
    draft_comment: str | None | MISSING = MISSING
    last_contact: VcoTimestamp | MISSING = MISSING

    profile_count: int | MISSING = MISSING
    is_all_mpg_cde: Annotated[bool | None | MISSING, Alias("isAllMPGCDE")] = MISSING
    is_all_ctrl_cde: Annotated[bool | None | MISSING, Alias("isAllCtrlCDE")] = MISSING


class GetEnterpriseCustomApps(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[EnterpriseCustomAppsParamsWith] | MISSING, Alias("with")] = (
        MISSING
    )


class CustomApp(VcoModel):
    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    enterprise_id: int | MISSING = MISSING
    logical_id: uuid.UUID
    status: CustomAppStatus
    status_modified: VcoTimestamp
    previous_data: CustomAppData | None | MISSING = MISSING
    data: CustomAppData
    version: str
    modified: VcoTimestamp

    profile_level_usage: list[Any] | MISSING = MISSING
    profile_level_usage_count: int | MISSING = MISSING
    edge_level_usage: list[Any] | MISSING = MISSING
    edge_level_usage_count: int | MISSING = MISSING


# --- get_enterprise (enterprise/getEnterprise) ---


class GetEnterprise(VcoModel):

    id_: Annotated[
        int | MISSING,
        Alias("id"),
        Field(description="Enterprise integer ID; alias of enterpriseId."),
    ] = MISSING
    enterprise_id: Annotated[
        int | MISSING,
        Field(
            description=(
                "Target enterprise integer ID. Inferred for an enterprise-scoped "
                "session; otherwise identify the target enterprise explicitly."
            )
        ),
    ] = MISSING
    enterprise_logical_id: Annotated[
        uuid.UUID | MISSING,
        Field(
            description="Target enterprise logical UUID; alternative to an integer ID."
        ),
    ] = MISSING
    with_: Annotated[
        list[GetEnterpriseWith] | MISSING,
        Alias("with"),
        Field(
            description="Related data to include; only enterpriseProxy is supported."
        ),
    ] = MISSING


class EnterpriseResult(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    name: str
    network_id: int | None | MISSING = MISSING
    gateway_pool_id: int | None | MISSING = MISSING
    alerts_enabled: Tinyint | None | MISSING = MISSING
    operator_alerts_enabled: Tinyint | None | MISSING = MISSING
    enterprise_proxy_id: int | None | MISSING = MISSING
    enterprise_proxy_name: str | None | MISSING = MISSING
    account_number: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    operator_id: int | None | MISSING = MISSING
    operator_profile_id: int | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    status_modified: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    bastion_state: str | None | MISSING = MISSING
    edition: str | None | MISSING = MISSING
    service_level: str | None | MISSING = MISSING
    # Optional includes
    enterprise_proxy: EnterpriseProxyResult | None | MISSING = MISSING
    edges: list[GetEnterpriseEdgesItem] | MISSING = MISSING
    links: list[EdgeLink] | MISSING = MISSING


# --- get_enterprise_users (enterprise/getEnterpriseUsers) ---


class GetEnterpriseUsers(VcoModel):

    enterprise_id: Annotated[
        int | MISSING,
        Field(
            description=(
                "Target enterprise integer ID. Inferred for an enterprise-scoped "
                "session; required for an operator or MSP selecting a customer."
            )
        ),
    ] = MISSING
    id_: Annotated[
        int | MISSING,
        Alias("id"),
        Field(description="Target enterprise integer ID; alias of enterpriseId."),
    ] = MISSING


class EnterpriseUser(VcoModel):

    id_: Annotated[int, Alias("id")]
    created: VcoTimestamp
    enterprise_id: int | None | MISSING = MISSING
    username: str | None | MISSING = MISSING
    email: str | None | MISSING = MISSING
    first_name: str | None | MISSING = MISSING
    last_name: str | None | MISSING = MISSING
    role_id: int | None | MISSING = MISSING
    role_name: str | None | MISSING = MISSING
    is_active: int | None | MISSING = MISSING
    is_locked: int | None | MISSING = MISSING
    is_native: int | None | MISSING = MISSING
    last_login: VcoTimestamp | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    user_type: str | None | MISSING = MISSING
    domain: str | None | MISSING = MISSING
    access_level: str | None | MISSING = MISSING


# --- get_enterprise_gateway_handoff (enterprise/getEnterpriseGatewayHandoff) ---


class GetEnterpriseGatewayHandoff(VcoModel):

    enterprise_id: int | MISSING = MISSING


class EnterpriseGatewayHandoffResult(VcoModel):

    type_: Annotated[str | None | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING


# --- get_enterprise_service_license (service/getEnterpriseServiceLicense) ---


class GetEnterpriseServiceLicense(VcoModel):

    enterprise_id: int | MISSING = MISSING


class EnterpriseServiceLicenseResult(VcoModel):

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    license_id: int | None | MISSING = MISSING
    sku: str | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    alias: str | None | MISSING = MISSING
    detail: str | None | MISSING = MISSING
    quota: str | None | MISSING = MISSING
    term_months: int | None | MISSING = MISSING
    start: VcoTimestamp | None | MISSING = MISSING
    end: VcoTimestamp | None | MISSING = MISSING
    edition: str | None | MISSING = MISSING
    bandwidth_tier: str | None | MISSING = MISSING
    active: Tinyint | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_capabilities (enterprise/getEnterpriseCapabilities) ---


class GetEnterpriseCapabilities(VcoModel):
    enterprise_id: Annotated[
        int | MISSING,
        Field(
            description=(
                "Target enterprise integer ID. Inferred for an enterprise-scoped "
                "session; required for an operator or MSP selecting a customer."
            )
        ),
    ] = MISSING


class EnterpriseCapabilitiesResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_addresses (enterprise/getEnterpriseAddresses) ---


class GetEnterpriseAddresses(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseAddressResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_all_alerts_recipients (enterprise/getEnterpriseAllAlertsRecipients) ---


class GetEnterpriseAllAlertsRecipients(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseAllAlertsRecipientsResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_rate_limits (enterprise/getEnterpriseRateLimits) ---


class GetEnterpriseRateLimits(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseRateLimitsResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_property (enterprise/getEnterpriseProperty) ---


class GetEnterpriseProperty(VcoModel):
    enterprise_id: int | MISSING = MISSING
    name: str
    id_: Annotated[int | None, Alias("id")] = None


class EnterprisePropertyResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_default_operator_configuration ---


class GetEnterpriseDefaultOperatorConfiguration(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseDefaultOperatorConfigurationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_distributed_cost_calculation ---


class GetEnterpriseDistributedCostCalculation(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseDistributedCostCalculationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_maximum_segments ---


class GetEnterpriseMaximumSegments(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseMaximumSegmentsResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_network_allocation ---


class GetEnterpriseNetworkAllocation(VcoModel):
    enterprise_id: int | MISSING = MISSING
    id_: Annotated[int, Alias("id")]
    network_id: int | MISSING = MISSING
    zone: str | MISSING = MISSING
    with_: Annotated[
        list[GetEnterpriseNetworkAllocationWith] | MISSING, Alias("with")
    ] = MISSING


class EnterpriseNetworkAllocationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_network_allocations ---


class GetEnterpriseNetworkAllocations(VcoModel):
    enterprise_id: int | MISSING = MISSING
    network_id: int | MISSING = MISSING
    name: str | MISSING = MISSING
    zone: str | MISSING = MISSING
    with_: Annotated[
        list[GetEnterpriseNetworkAllocationWith] | MISSING, Alias("with")
    ] = MISSING


class EnterpriseNetworkAllocationsResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_operator_configuration ---


class GetEnterpriseOperatorConfiguration(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[GetEnterpriseOperatorConfigurationWith | None, Alias("with")] = (
        None
    )
    no_data: bool | None = None


class EnterpriseOperatorConfigurationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_specific_operator_configurations ---


class GetEnterpriseSpecificOperatorConfigurations(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_modules: bool | MISSING = MISSING
    with_edge_count: bool | MISSING = MISSING


class EnterpriseSpecificOperatorConfigurationsResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprises_missing_gateway ---


class GetEnterprisesMissingGateway(VcoModel):
    gateway_type: str | None = None


class EnterprisesMissingGatewayResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprises_with_property ---


class GetEnterprisesWithProperty(VcoModel):
    name: str
    value: str
    enterprise_ids: list[int] | MISSING = MISSING


class EnterprisesWithPropertyResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_auth_migration_status ---


class GetAuthMigrationStatus(VcoModel):
    enterprise_ids: list[int]


class AuthMigrationStatusResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_user_export_status ---


class GetUserExportStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING


class UserExportStatusResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_api_tokens (enterprise/getApiTokens) ---


class GetEnterpriseApiTokens(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseApiTokenResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_security_objects (enterprise/getSecurityObjects) ---


class GetEnterpriseSecurityObjects(VcoModel):
    enterprise_id: int | MISSING = MISSING
    type_: Annotated[SecurityObjectType, Alias("type")]
    with_: Annotated[list[GetEnterpriseSecurityObjectsWith] | None, Alias("with")] = (
        None
    )


class EnterpriseSecurityObjectResult(VcoModel):
    """A security object (EFS property) associated with an enterprise."""

    model_config = ConfigDict(extra="allow")

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    operator_id: int | None | MISSING = MISSING
    network_id: int | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    parent_group_id: int | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    object_: Annotated[str | None | MISSING, Alias("object")] = MISSING
    name: str | None | MISSING = MISSING
    type_: Annotated[str | None | MISSING, Alias("type")] = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    alerts_enabled: int | None | MISSING = MISSING
    operator_alerts_enabled: int | None | MISSING = MISSING
    status: str | None | MISSING = MISSING
    data: dict[str, Any] | None | MISSING = MISSING
    version: str | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING


# --- get_enterprise_alert_configurations ---


class GetEnterpriseAlertConfigurations(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseAlertConfigurationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_route_table ---


class GetEnterpriseRouteTable(VcoModel):
    enterprise_id: int | MISSING = MISSING
    profiles: list[int] | MISSING = MISSING
    filter: Annotated[dict[str, Any] | MISSING, Alias("filter")] = MISSING


class EnterpriseRouteTableProfile(VcoModel):
    id_: Annotated[int, Alias("id")]
    name: str
    description: str


class EnterpriseRouteTableSubnetString(VcoModel):
    subnet: str
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")]

    @model_validator(mode="before")
    @classmethod
    def split_subnet_str(cls, data: Any) -> Any:
        if isinstance(data, str) and ":" in data:
            try:
                netwk, segment = data.rsplit(":", 1)
                return {"subnet": netwk, "segment_id": segment}
            except ValueError:
                pass
        return data


class EnterpriseRouteTableRoute(VcoModel):
    cidr_ip: str
    cidr_prefix: Annotated[int, Ge(0), Le(128)]
    created: VcoTimestamp
    description: str | None
    id: int
    logical_id: uuid.UUID
    modified: VcoTimestamp
    pinned: bool
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")]


class EnterpriseRouteTableExit(VcoModel):
    advertise: Tinyint | MISSING = MISSING
    area: int | MISSING = MISSING
    attributes: dict[str, Any] | MISSING = MISSING
    cost: int
    created: VcoTimestamp | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    edge_name: str | MISSING = MISSING
    enterprise_object_id: int | None | MISSING = MISSING
    entity: str | MISSING = MISSING
    exit_type: str
    filtered: int | MISSING = MISSING
    id: int | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    is_owner: int | MISSING = MISSING
    learned_by: int | MISSING = MISSING
    metric: int | MISSING = MISSING
    modified: VcoTimestamp | MISSING = MISSING
    neighbor_id: str | MISSING = MISSING
    neighbor_ip: str | MISSING = MISSING
    neighbor_tag: str | MISSING = MISSING
    next_hop_ip: str | MISSING = MISSING
    order: int
    profile_id: int | MISSING = MISSING
    protocol: str | MISSING = MISSING
    route_id: int | MISSING = MISSING
    route_type: str | MISSING = MISSING
    segment_id: Annotated[int | MISSING, Resolvable(SegmentIdentity, "segment_id")] = (
        MISSING
    )
    state: str | MISSING = MISSING
    tag: Any | MISSING = MISSING
    type_: Annotated[str, Alias("type")]


class EnterpriseRouteTableEntry(VcoModel):
    eligable_exits: list[Any]
    ip_version: Literal["IPv4", "IPv6"]
    learned_route: EnterpriseRouteTableRoute | MISSING = MISSING
    preferred_exits: list[EnterpriseRouteTableExit]
    subnet: EnterpriseRouteTableSubnetString


class EnterpriseRouteTableResult(VcoModel):
    profiles: list[EnterpriseRouteTableProfile] = []
    subnets: list[EnterpriseRouteTableEntry] = []


# --- get_enterprise_route_configuration ---


class GetEnterpriseRouteConfiguration(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseRouteConfigurationResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- get_enterprise_use_nsd_policy ---


class GetEnterpriseUseNsdPolicy(VcoModel):
    enterprise_id: int | MISSING = MISSING


class EnterpriseUseNsdPolicyResult(VcoModel):
    model_config = ConfigDict(extra="allow")


# --- Cluster Hub Enable/Disable ---


class EnableClusterHub(VcoModel):
    """Designate a cluster as a VPN hub on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class EnableClusterToEdgeBridge(VcoModel):
    """Designate a cluster hub as a site-to-site bridge on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class EnableClusterBackHaul(VcoModel):
    """Designate a cluster hub for backhaul on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableClusterHub(VcoModel):
    """Remove VPN hub designation from a cluster on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableClusterToEdgeBridge(VcoModel):
    """Remove site-to-site bridge designation from a cluster on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableClusterBackHaul(VcoModel):
    """Remove backhaul designation from a cluster on a profile."""

    id_: Annotated[int, Alias("id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING
