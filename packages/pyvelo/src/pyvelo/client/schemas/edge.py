import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator
from pydantic_core import MISSING

from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.edge import (
    ActivationState,
    EdgeBastionState,
    EdgeServiceState,
    EdgeState,
    EndpointPkiMode,
    GetEdgeWith,
    HaMode,
    HaRole,
    HaState,
    HaType,
    LinkMode,
    LinkNetworkType,
    LinkState,
    ModelNumber,
    NetworkSide,
    SdwanPeerType,
    SortDirection,
)
from pyvelo.client.enums.monitoring import TunnelState, TunnelType
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    ImageUpdateData,
    ManagementPlaneData,
    QosData,
    WanData,
)
from pyvelo.client.models.configuration.control_plane.root import ControlPlaneData
from pyvelo.client.schemas.configuration import (
    Configuration,
)
from pyvelo.core import (
    Alias,
    NoneIsMissing,
    Tinyint,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.base import ApiInterval
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import (
    EdgeIdentity,
    LicenseIdentity,
    ProfileIdentity,
    SegmentIdentity,
)


class EdgeHaClusterData(VcoModel):
    cluster_id: int | None | MISSING = MISSING
    cluster_name: str | None | MISSING = MISSING


class EdgeHaClusterInfo(VcoModel):
    data: EdgeHaClusterData | None | MISSING = MISSING
    type_: Annotated[Literal["ACTIVE_ACTIVE"], Alias("type")]


class EdgeHaActiveStandbyData(VcoModel):
    ha_last_contact: VcoTimestamp | None | MISSING = MISSING
    ha_mode: HaMode | None | MISSING = MISSING
    ha_previous_state: HaState | None | MISSING = MISSING
    ha_serial_number: str | None | MISSING = MISSING
    ha_state: HaState | None | MISSING = MISSING
    ha_wifi_capability_mismatch: int | None | MISSING = MISSING


class EdgeHaActiveStandbyInfo(VcoModel):
    data: EdgeHaActiveStandbyData | None | MISSING = MISSING
    type_: Annotated[Literal["ACTIVE_STANDBY"], Alias("type")]


class EdgeHaVrrpInfo(VcoModel):
    data: list[dict[str, Any]] | None | MISSING = MISSING
    type_: Annotated[Literal["VRRP"], Alias("type")]


class EdgeHaNone(VcoModel):
    data: dict[str, Any] | None | MISSING = MISSING
    type_: Annotated[Literal["NONE"], Alias("type")]


type EdgeHaInfo = Annotated[
    EdgeHaClusterInfo | EdgeHaActiveStandbyInfo | EdgeHaVrrpInfo | EdgeHaNone,
    Field(discriminator="type_"),
]


class EdgeObject(VcoModel):
    activation_key: str | None | MISSING = MISSING
    activation_key_expires: VcoTimestamp | None | MISSING = MISSING
    activation_state: ActivationState | None | MISSING = MISSING
    activation_time: VcoTimestamp | None | MISSING = MISSING
    alerts_enabled: Tinyint | None | MISSING = MISSING
    bastion_state: EdgeBastionState | None | MISSING = MISSING
    build_number: str | None | MISSING = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    custom_info: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING
    device_family: str | None | MISSING = MISSING
    device_id: str | None | MISSING = MISSING
    dns_name: str | None | MISSING = MISSING
    edge_state: EdgeState | None | MISSING = MISSING
    edge_state_time: VcoTimestamp | None | MISSING = MISSING
    endpoint_pki_mode: EndpointPkiMode | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_logical_id: uuid.UUID | None | MISSING = MISSING
    factory_software_version: str | None | MISSING = MISSING
    factory_build_number: str | None | MISSING = MISSING
    platform_firmware_version: str | None | MISSING = MISSING
    platform_build_number: str | None | MISSING = MISSING
    modem_firmware_version: str | None | MISSING = MISSING
    modem_build_number: str | None | MISSING = MISSING
    lte_region: str | None | MISSING = MISSING
    ha_last_contact: VcoTimestamp | None | MISSING = MISSING
    ha_previous_state: HaState | None | MISSING = MISSING
    ha_serial_number: str | None | MISSING = MISSING
    ha_state: HaState | None | MISSING = MISSING
    ha_mode: str | None | MISSING = MISSING
    id_: Annotated[
        int | None | MISSING, Alias("id"), Resolvable(EdgeIdentity, "id")
    ] = MISSING
    is_live: int | None | MISSING = MISSING
    last_contact: VcoTimestamp | None | MISSING = MISSING
    logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    model_number: str | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    operator_alerts_enabled: Tinyint | None | MISSING = MISSING
    self_mac_address: str | None | MISSING = MISSING
    serial_number: str | None | MISSING = MISSING
    service_state: EdgeServiceState | None | MISSING = MISSING
    service_up_since: VcoTimestamp | None | MISSING = MISSING
    site_id: int | None | MISSING = MISSING
    software_updated: VcoTimestamp | None | MISSING = MISSING
    software_version: str | None | MISSING = MISSING
    system_up_since: VcoTimestamp | None | MISSING = MISSING
    standby_system_up_since: VcoTimestamp | None | MISSING = MISSING
    standby_service_up_since: VcoTimestamp | None | MISSING = MISSING
    standby_software_version: str | None | MISSING = MISSING
    standby_factory_software_version: str | None | MISSING = MISSING
    standby_factory_build_number: str | None | MISSING = MISSING
    standby_build_number: str | None | MISSING = MISSING
    standby_model_number: str | None | MISSING = MISSING
    standby_device_id: str | None | MISSING = MISSING
    ha_wifi_capability_mismatch: bool | None | MISSING = MISSING


class GetEdgeCertificates(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = Field(
        MISSING, description="VCO enterprise ID; omit to use the session enterprise"
    )


class EdgeCertificate(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: AwareDatetime | None | MISSING = MISSING
    csr_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_serial_number: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    certificate: str | None | MISSING = MISSING
    serial_number: str | None | MISSING = MISSING
    subject_key_id: str | None | MISSING = MISSING
    authority_key_id: str | None | MISSING = MISSING
    finger_print: str | None | MISSING = MISSING
    finger_print256: str | None | MISSING = MISSING
    valid_from: AwareDatetime | None | MISSING = MISSING
    valid_to: AwareDatetime | None | MISSING = MISSING


class Site(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: AwareDatetime | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    contact_name: str | None | MISSING = MISSING
    contact_phone: str | None | MISSING = MISSING
    contact_mobile: str | None | MISSING = MISSING
    contact_email: str | None | MISSING = MISSING
    street_address: str | None | MISSING = MISSING
    street_address2: str | None | MISSING = MISSING
    city: str | None | MISSING = MISSING
    state: str | None | MISSING = MISSING
    postal_code: str | None | MISSING = MISSING
    country: str | None | MISSING = MISSING
    lat: float | None | MISSING = MISSING
    lon: float | None | MISSING = MISSING
    timezone: str | None | MISSING = MISSING
    locale: str | None | MISSING = MISSING
    shipping_same_as_location: Tinyint | None | MISSING = MISSING
    shipping_contact_name: str | None | MISSING = MISSING
    shipping_address: str | None | MISSING = MISSING
    shipping_address2: str | None | MISSING = MISSING
    shipping_city: str | None | MISSING = MISSING
    shipping_state: str | None | MISSING = MISSING
    shipping_country: str | None | MISSING = MISSING
    shipping_postal_code: str | None | MISSING = MISSING
    modified: AwareDatetime | None | MISSING = MISSING


class EdgeLicense(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
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


class CreateEdge(VcoModel):
    enterprise_id: int | MISSING = MISSING
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    name: str | MISSING = MISSING
    serial_number: str | MISSING = MISSING
    model_number: ModelNumber
    description: str | MISSING = MISSING
    site: Site | MISSING = MISSING
    ha_enabled: bool | MISSING = MISSING
    endpoint_pki_mode: EndpointPkiMode | MISSING = MISSING
    edge_license_id: Annotated[int, Resolvable(LicenseIdentity, "id")]
    custom_info: str | MISSING = MISSING


class GeneratedCertificate(VcoModel):
    certificate: str | None | MISSING = MISSING
    ca_certificate: str | None | MISSING = MISSING
    private_key: str | None | MISSING = MISSING
    private_key_password: str | None | MISSING = MISSING
    csr: str | None | MISSING = MISSING


class CreateEdgeResult(VcoModel):
    id_: Annotated[int, Alias("id")]
    activation_key: str
    generated_certificate: GeneratedCertificate | None | MISSING = MISSING


class DeleteEdge(VcoModel):
    """Request to delete an edge."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING


class RequestEdgeHaFailover(VcoModel):
    """Request to trigger HA failover on an edge."""

    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID of the HA-enabled edge to fail over"
    )
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING


class GetEdgeConfigurationStack(VcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )


class GetEdgeSdwanPeersSortBy(VcoModel):
    attribute: Literal["pathQoe"] = "pathQoe"
    type_: Annotated[SortDirection, Alias("type")] = SortDirection.ASC


class GetEdgeSdwanPeers(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING
    interval: ApiInterval
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING
    filter_spec: Annotated[bool | MISSING, Alias("_filterSpec")] = MISSING
    limit: int | MISSING = Field(default=MISSING)
    sort_by: tuple[GetEdgeSdwanPeersSortBy] | MISSING = Field(
        default_factory=lambda: (GetEdgeSdwanPeersSortBy(),)
    )


class PathStatusCounts(VcoModel):
    stable: int
    unstable: int
    standby: int
    dead: int
    unknown: int
    total: int


class EdgeSdwanPeerEntry(VcoModel):
    """An SD-WAN peer visible from an Edge with path quality summary."""

    device_logical_id: uuid.UUID = Field(
        description="Logical ID of the peer Edge or Hub"
    )
    peer_name: str | None = Field(description="Display name of the peer device")
    description: str | None = Field(
        description="Site or device description of the peer"
    )
    peer_type: SdwanPeerType = Field(
        description="Peer role in the overlay (BRANCH, HUB, or GATEWAY)"
    )
    peer_ha_type: Annotated[HaType | None | MISSING, Alias("peerHAType")] = MISSING
    cluster_id: int | None | MISSING = MISSING
    path_status_count: PathStatusCounts = Field(
        description="Count of paths in each quality state (GOOD, FAIR, POOR, DEAD)"
    )
    path_qoe: float = Field(
        description="Composite QoE score across all paths (0-10 scale)"
    )
    score_after_voice: float = Field(
        description="Predicted MOS score for voice traffic across best path"
    )
    score_after_video: float = Field(
        description="Predicted quality score for video traffic across best path"
    )
    score_after_trans: float = Field(
        description="Predicted quality score for transactional traffic across best path"
    )


# --- get_edge (edge/getEdge) ---


class GetEdge(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING
    id_: Annotated[int | MISSING, Alias("id"), Resolvable(EdgeIdentity, "id")] = Field(
        default=MISSING, description="VCO Edge ID"
    )
    with_: Annotated[list[GetEdgeWith] | MISSING, Alias("with")] = MISSING
    logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    activation_key: str | MISSING = MISSING
    name: str | MISSING = MISSING


class EdgeLink(VcoModel):
    """"""

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: AwareDatetime | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    logical_id: str | None | MISSING = MISSING
    internal_id: uuid.UUID | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    ip_v6_address: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    org: str | None | MISSING = MISSING
    link_mode: LinkMode | None | MISSING = MISSING
    network_side: NetworkSide | None | MISSING = MISSING
    network_type: LinkNetworkType | None | MISSING = MISSING
    display_name: str | None | MISSING = MISSING
    alerts_enabled: Tinyint | None | MISSING = MISSING
    operator_alerts_enabled: Tinyint | None | MISSING = MISSING
    state: LinkState | None | MISSING = MISSING
    back_up_state: str | None | MISSING = MISSING
    vpn_state: str | None | MISSING = MISSING
    last_active: AwareDatetime | None | MISSING = MISSING
    service_state: str | None | MISSING = MISSING
    modified: AwareDatetime | None | MISSING = MISSING
    last_event: VcoTimestamp | None | MISSING = MISSING
    last_event_state: str | None | MISSING = MISSING
    link_policy: str | None | MISSING = MISSING
    service_groups: list[str] | None | MISSING = MISSING
    lat: float | None | MISSING = MISSING
    lon: float | None | MISSING = MISSING


class GetEdgeResult(EdgeObject):
    """"""

    site: Site | None | MISSING = MISSING
    links: list[EdgeLink] | None | MISSING = MISSING
    recent_links: list[EdgeLink] | None | MISSING = MISSING
    configuration: Configuration | None | MISSING = MISSING
    enterprise: dict[str, Any] | None | MISSING = MISSING
    self_healing: bool | None | MISSING = MISSING
    certificates: list[EdgeCertificate] | None | MISSING = MISSING
    ha: EdgeHaInfo | None | MISSING = MISSING
    licenses: list[EdgeLicense] | None | MISSING = MISSING
    is_hub: bool | None | MISSING = MISSING


# --- get_edge_gateway_assignment_view (edge/getEdgeGatewayAssignmentsView) ---


class GetEdgeGatewayAssignmentsView(VcoModel):
    """"""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = MISSING


class GatewayAssignmentsEntry(VcoModel):
    """A single gateway entry in an edge's gateway assignment view."""

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    activation_state: ActivationState | None | MISSING = MISSING
    gateway_state: str | None | MISSING = MISSING
    service_state: str | None | MISSING = MISSING


class DataCenterGatewayEntry(VcoModel):
    """A data-center gateway with redundancy indicator."""

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    activation_state: ActivationState | None | MISSING = MISSING
    gateway_state: str | None | MISSING = MISSING
    service_state: str | None | MISSING = MISSING
    is_redundant: bool | None | MISSING = MISSING


class GatewayHandOffs(VcoModel):
    """Gateway hand-off configuration."""

    gateways: dict[str, Any] | None | MISSING = MISSING


class EdgeGatewayAssignmentResult(VcoModel):
    """Edge gateway assignment view showing primary, secondary, and data-center gateways."""

    model_config = ConfigDict(extra="allow")

    primary: GatewayAssignmentsEntry | None | MISSING = MISSING
    secondary: GatewayAssignmentsEntry | None | MISSING = MISSING
    super_gateway: GatewayAssignmentsEntry | None | MISSING = MISSING
    super_alt_gateway: GatewayAssignmentsEntry | None | MISSING = MISSING
    data_centers: list[DataCenterGatewayEntry] | None | MISSING = MISSING
    hand_offs: GatewayHandOffs | None | MISSING = MISSING
    primary_cws: GatewayAssignmentsEntry | None | MISSING = MISSING
    secondary_cws: GatewayAssignmentsEntry | None | MISSING = MISSING
    primary_wss: GatewayAssignmentsEntry | None | MISSING = MISSING
    secondary_wss: GatewayAssignmentsEntry | None | MISSING = MISSING


# --- get_edge_configuration_modules (edge/getEdgeConfigurationModules) ---


class GetEdgeConfigurationModules(VcoModel):
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    enterprise_id: int | MISSING = MISSING
    enterprise_logical_id: uuid.UUID | MISSING = MISSING
    modules: list[ConfigurationModuleName] | MISSING = MISSING

    @model_validator(mode="after")
    def _require_edge_identifier(self) -> Self:
        if self.edge_id is MISSING and self.edge_logical_id is MISSING:
            raise ValueError("edgeId or edgeLogicalId is required")
        return self


class CompositeModuleBase(VcoModel):
    version: str
    schema_version: str
    type_: Annotated[ConfigurationModuleType, Alias("type")]


class CompositeDeviceSettingsModule(CompositeModuleBase):
    data: DeviceSettingsData


class CompositeFirewallModule(CompositeModuleBase):
    data: FirewallData


class CompositeQosModule(CompositeModuleBase):
    data: QosData


class CompositeWanModule(CompositeModuleBase):
    data: WanData


class CompositeControlPlaneModule(CompositeModuleBase):
    data: ControlPlaneData


class CompositeManagementPlaneModule(CompositeModuleBase):
    data: ManagementPlaneData


class CompositeImageUpdateModule(CompositeModuleBase):
    data: ImageUpdateData


class EdgeConfigurationModulesResult(VcoModel):
    device_settings: CompositeDeviceSettingsModule | None | MISSING = MISSING
    firewall: CompositeFirewallModule | None | MISSING = MISSING
    qos: Annotated[CompositeQosModule | None | MISSING, Alias("QOS")] = MISSING
    wan: Annotated[CompositeWanModule | None | MISSING, Alias("WAN")] = MISSING
    control_plane: CompositeControlPlaneModule | None | MISSING = MISSING
    management_plane: CompositeManagementPlaneModule | None | MISSING = MISSING
    image_update: CompositeImageUpdateModule | None | MISSING = MISSING
    analytics_settings: dict[str, Any] | None | MISSING = MISSING
    atp_metadata: dict[str, Any] | None | MISSING = MISSING


# --- get_edge_nvs_tunnel_status (monitoring/getEnterpriseEdgeNvsTunnelStatus) ---


class GetEdgeNvsTunnelStatus(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    tag: str | MISSING = MISSING
    provider_object_id: int | MISSING = MISSING


class EdgeNvsTunnelStatusItem(VcoModel):
    """"""

    data_key: str | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_name: str | None | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_name: str | None | MISSING = MISSING
    nsd_name: str | None | MISSING = MISSING
    state: TunnelState | None | MISSING = MISSING
    state_time: VcoTimestamp | None | MISSING = MISSING
    tunnel_type: TunnelType | None | MISSING = MISSING
    local_ip: str | None | MISSING = MISSING
    remote_ip: str | None | MISSING = MISSING
    provider_object_id: int | None | MISSING = MISSING


# --- get_client_visibility_mode (edge/getClientVisibilityMode) ---


class GetClientVisibilityMode(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )


class ClientVisibilityModeResult(VcoModel):
    """"""

    mode: str | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


# --- get_ha_interfaces (edge/getHaInterfaces) ---


class GetHaInterfaces(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING
    edge_logical_id: Annotated[uuid.UUID, Resolvable(EdgeIdentity, "logical_id")] = (
        Field(description="Edge logical UUID identifier")
    )
    role: HaRole | MISSING = MISSING


class HaInterfaceItem(VcoModel):
    """A single HA interface entry."""

    model_config = ConfigDict(extra="allow")

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    role: str | None | MISSING = MISSING


# --- get_interconnect_gateways (edge/getInterconnectGateways) ---


class GetInterconnectGateways(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING


class InterconnectGatewayItem(VcoModel):
    """"""

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    logical_id: uuid.UUID | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    state: str | None | MISSING = MISSING
    description: str | None | MISSING = MISSING


# --- get_secure_device_secrets_option (edge/getSecureDeviceSecretsOption) ---


class GetSecureDeviceSecretsOption(VcoModel):
    """"""

    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    edge_ids: list[int] | MISSING = MISSING


class SecureDeviceSecretsOptionResult(VcoModel):
    """"""

    model_config = ConfigDict(extra="allow")


class EdgeSdwanPeersResult(VcoModel):
    """"""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def handle_empty_list(cls, data: Any) -> Any:
        if isinstance(data, list) and len(data) == 0:
            return {}
        return data


# --- VPN Hub Enable/Disable ---


class EnableEdgeHub(VcoModel):
    """Designate an edge as a VPN hub on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class EnableEdgeToEdgeBridge(VcoModel):
    """Designate an edge hub as a site-to-site bridge on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class EnableEdgeBackHaul(VcoModel):
    """Designate an edge hub for backhaul on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableEdgeHub(VcoModel):
    """Remove VPN hub designation from an edge on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableEdgeToEdgeBridge(VcoModel):
    """Remove site-to-site bridge designation from an edge on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING


class DisableEdgeBackHaul(VcoModel):
    """Remove backhaul designation from an edge on a profile."""

    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")]
    configuration_id: Annotated[int, Resolvable(ProfileIdentity, "id")]
    module_id: int
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")]
    enterprise_id: int | MISSING = MISSING
