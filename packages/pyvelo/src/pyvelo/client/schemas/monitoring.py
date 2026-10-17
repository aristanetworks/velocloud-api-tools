import uuid
from typing import Annotated, Any, ClassVar, Self

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_core import MISSING

from pyvelo.client.enums.edge import (
    ActivationState,
    EdgeState,
    HaRole,
    HaState,
    LinkBackupState,
    LinkState,
    SortDirection,
)
from pyvelo.client.enums.metrics import (
    GatewayStatusMetric,
    HealthStatsMetric,
    NniMetric,
    StatusMetric,
)
from pyvelo.client.enums.monitoring import (
    BfdPeerState,
    BgpPeerState,
    DegradationDetailsSortAttribute,
    DegradationViewBy,
    DegradationWith,
    GatewayState,
    GetAggregateEdgeNvsTunnelStatusWith,
    GetAggregateEnterpriseEventsWith,
    NvsProviderType,
    NvsTunnelTag,
    TunnelState,
)
from pyvelo.core import (
    Alias,
    ApiFilter,
    ApiFilters,
    ApiFiltersAnd,
    ApiFiltersOr,
    ApiInterval,
    CoerceInt,
    PaginatedVcoModel,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import (
    EdgeIdentity,
    GatewayIdentity,
    GatewayLogicalId,
    LinkIdentity,
    SegmentIdentity,
)

_LINK_INSIGHTS_METADATA_FILTER_FIELDS = frozenset(
    {
        "linkName",
        "edgeName",
        "interface",
        "linkIpAddress",
        "isp",
        "wanLinkMode",
        "wanLinkType",
        "trafficType",
        "linkLogicalId",
        "edgeLogicalId",
    }
)
_LINK_FLAP_SUMMARY_FILTER_FIELDS = _LINK_INSIGHTS_METADATA_FILTER_FIELDS - {
    "trafficType"
}
_LINK_DEGRADATION_DETAIL_FILTER_FIELDS = _LINK_INSIGHTS_METADATA_FILTER_FIELDS | {
    "degradation.before.fairMinutes",
    "degradation.before.criticalMinutes",
    "degradation.after.fairMinutes",
    "degradation.after.criticalMinutes",
    "packetLossRx",
    "packetLossTx",
    "jitterRx",
    "jitterTx",
    "latencyRx",
    "latencyTx",
    "uptimePct",
    "secondsUp",
    "secondsDown",
    "secondsUnknown",
    "totalFlaps",
}

# --- Shared sub-models ---


class MetricSummary(VcoModel):
    min_: Annotated[float | None | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | None | MISSING, Alias("max")] = MISSING
    average: float | None | MISSING = MISSING


class EdgeHealthStats(VcoModel):
    model_config = ConfigDict(extra="allow")

    cpu_pct: MetricSummary | MISSING = MISSING
    memory_pct: MetricSummary | MISSING = MISSING
    tunnel_count: MetricSummary | MISSING = MISSING
    tunnel_count_v6: MetricSummary | MISSING = MISSING
    flow_count: MetricSummary | MISSING = MISSING
    handoff_queue_drops: MetricSummary | MISSING = MISSING
    cpu_core_temp: MetricSummary | MISSING = MISSING
    route_count: MetricSummary | MISSING = MISSING
    route_count_v6: MetricSummary | MISSING = MISSING
    nat_count: MetricSummary | MISSING = MISSING
    flow_util_pct: MetricSummary | MISSING = MISSING
    tunnel_util_pct: MetricSummary | MISSING = MISSING


class GatewayHealthStats(VcoModel):
    model_config = ConfigDict(extra="allow")

    cpu_pct: MetricSummary | MISSING = MISSING
    memory_pct: MetricSummary | MISSING = MISSING
    tunnel_count: MetricSummary | MISSING = MISSING
    tunnel_count_v6: MetricSummary | MISSING = MISSING
    flow_count: MetricSummary | MISSING = MISSING
    handoff_queue_drops: MetricSummary | MISSING = MISSING
    connected_edges: MetricSummary | MISSING = MISSING


class TunnelStateCounts(VcoModel):
    up: int = 0
    down: int = 0
    unknown: int = 0


class NvsDataCenter(VcoModel):
    model_config = ConfigDict(extra="allow")

    name: str | None | MISSING = MISSING
    public_ip: str | None | MISSING = MISSING


class AggregateEventItem(VcoModel):
    model_config = ConfigDict(extra="allow")

    event: str | None | MISSING = MISSING
    category: str | None | MISSING = MISSING
    severity: str | None | MISSING = MISSING
    message: str | None | MISSING = MISSING
    event_time: VcoTimestamp | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    enterprise_name: str | None | MISSING = MISSING


class EventAggregateMetadata(VcoModel):
    model_config = ConfigDict(extra="allow")

    limit: int | None | MISSING = MISSING
    more: bool | None | MISSING = MISSING


class NniIdentifier(VcoModel):
    gateway_id: str | None | MISSING = MISSING
    nni_id: str | None | MISSING = MISSING


# --- Existing schemas (getEnterpriseEdgeLinkStatus) ---


class GetEnterpriseEdgeLinkStatus(VcoModel):
    network_id: int = 1
    enterprise_proxy_id: int | MISSING = MISSING
    enterprises: list[int] | MISSING = MISSING
    links: bool = False
    detailed: bool | MISSING = MISSING


class GetEnterpriseEdgeLinkStatusResultItem(VcoModel):
    # --- Base edge fields (always returned) ---
    enterprise_name: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    edge_serial_number: str | None | MISSING = MISSING
    edge_ha_serial_number: str | None | MISSING = MISSING
    edge_state: EdgeState | None | MISSING = MISSING
    edge_last_contact: VcoTimestamp | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_system_up_since: VcoTimestamp | None | MISSING = MISSING
    edge_service_up_since: VcoTimestamp | None | MISSING = MISSING
    edge_model_number: str | None | MISSING = MISSING
    edge_latitude: float | None | MISSING = MISSING
    edge_longitude: float | None | MISSING = MISSING

    # --- Operator-user only base fields ---
    enterprise_proxy_id: int | None | MISSING = MISSING
    enterprise_proxy_name: str | None | MISSING = MISSING

    # --- Link fields (when links=true) ---
    internal_id: str | None | MISSING = MISSING
    display_name: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    link_state: LinkState | None | MISSING = MISSING
    link_last_active: VcoTimestamp | None | MISSING = MISSING
    link_vpn_state: str | None | MISSING = MISSING
    link_id: Annotated[int | None | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    link_ip_address: str | None | MISSING = MISSING
    link_ip_v6_address: str | None | MISSING = MISSING

    # --- Operator-user link fields (when links=true) ---
    link_mode: str | None | MISSING = MISSING
    link_backup_state: LinkBackupState | None | MISSING = MISSING

    # --- Detailed fields (when detailed=true) ---
    enterprise_logical_id: uuid.UUID | None | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING

    # --- Detailed operator-user fields (when detailed=true) ---
    edge_created: VcoTimestamp | None | MISSING = MISSING
    edge_modified: VcoTimestamp | None | MISSING = MISSING
    activation_time: VcoTimestamp | None | MISSING = MISSING
    software_version: str | None | MISSING = MISSING
    build_number: str | None | MISSING = MISSING
    software_updated: VcoTimestamp | None | MISSING = MISSING
    is_live: int | None | MISSING = MISSING
    site_id: int | None | MISSING = MISSING
    activation_state: ActivationState | None | MISSING = MISSING
    factory_software_version: str | None | MISSING = MISSING
    factory_build_number: str | None | MISSING = MISSING
    timezone: str | None | MISSING = MISSING

    # --- Detailed link fields (when detailed=true, links=true) ---
    link_logical_id: str | None | MISSING = MISSING

    # --- Detailed operator-user link fields ---
    link_created: VcoTimestamp | None | MISSING = MISSING
    link_modified: VcoTimestamp | None | MISSING = MISSING


# --- getEnterpriseEdgeStatus ---


class GetEnterpriseEdgeStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_ids: list[int] | MISSING = MISSING
    role: HaRole | MISSING = MISSING
    time: str | MISSING = MISSING
    sort: StatusMetric | MISSING = MISSING
    limit: int | MISSING = MISSING
    metrics: list[StatusMetric] | MISSING = MISSING


class GetEnterpriseEdgeStatusResultItem(VcoModel):
    """Aggregate health status for a single Edge in an enterprise."""

    id_: Annotated[
        int | None | MISSING, Alias("id"), Field(description="VCO numeric identifier")
    ] = MISSING
    enterprise_id: int | None | MISSING = Field(
        MISSING, description="Parent enterprise numeric ID"
    )
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = Field(
        MISSING, description="Edge numeric ID"
    )
    edge_name: str | None | MISSING = Field(
        MISSING, description="Configured display name of the Edge"
    )
    edge_state: EdgeState | None | MISSING = Field(
        MISSING,
        description="Current operational state (CONNECTED, OFFLINE, DEGRADED, etc.)",
    )
    edge_model_number: str | None | MISSING = Field(
        MISSING, description="Hardware model (e.g. edge510, edge1000, virtual)"
    )
    edge_software_version: str | None | MISSING = Field(
        MISSING, description="Running VeloCloud Edge OS version"
    )
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = Field(MISSING, description="Stable UUID used for configuration references")
    ha_state: HaState | None | MISSING = Field(
        MISSING, description="HA pairing state (UNCONFIGURED, READY, FAILED, etc.)"
    )
    health_stats: EdgeHealthStats | None | MISSING = Field(
        MISSING,
        description="Aggregate health metric summaries (CPU, memory, tunnels, flows)",
    )


# --- getEnterpriseBgpPeerStatus ---


class GetEnterpriseBgpPeerStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING


class GetEnterpriseBfdPeerStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING


class BgpPeerData(VcoModel):
    """Nested BGP neighbor detail inside a gateway enterprise record.

    Wire-format note: VCO returns ``msgRcvd``, ``msgSent``, and ``pfxRcvd``
    as JSON numbers. ``neighborAS`` stays quoted (ASNs may be dotted, e.g.
    "65002.65530"). ``CoerceInt`` accepts historical quoted-int payloads
    for backward compat.
    """

    enterprise_logical_id: uuid.UUID | None | MISSING = MISSING
    segment_id: Annotated[
        int | None | MISSING, Resolvable(SegmentIdentity, "segment_id")
    ] = MISSING
    neighbor_ip: str | None | MISSING = MISSING
    neighbor_as: Annotated[str | None | MISSING, Alias("neighborAS")] = MISSING
    msg_rcvd: Annotated[int | None | MISSING, CoerceInt] = MISSING
    msg_sent: Annotated[int | None | MISSING, CoerceInt] = MISSING
    up_down_time: str | None | MISSING = MISSING
    state: BgpPeerState | None | MISSING = MISSING
    pfx_rcvd: Annotated[int | None | MISSING, CoerceInt] = MISSING


class GetEnterpriseBgpPeerStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    enterprise_id: int | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    gateway_name: str | None | MISSING = MISSING
    gateway_logical_id: GatewayLogicalId | None | MISSING = MISSING
    gateway_state: GatewayState | None | MISSING = MISSING
    principle: str | None | MISSING = MISSING
    enterprise_object_id: int | None | MISSING = MISSING
    timestamp: VcoTimestamp | None | MISSING = MISSING
    tag: str | None | MISSING = MISSING
    data_key: str | None | MISSING = MISSING
    state: BgpPeerState | None | MISSING = MISSING
    data: BgpPeerData | None | MISSING = MISSING


class BfdPeerTimer(VcoModel):
    receive: str | None | MISSING = MISSING
    transmit: str | None | MISSING = MISSING


class BfdPeerStatusRecord(VcoModel):
    enterprise_logical_id: str | None | MISSING = MISSING
    segment_id: str | None | MISSING = MISSING
    peer_address: str | None | MISSING = MISSING
    local_address: str | None | MISSING = MISSING
    state: BfdPeerState | None | MISSING = MISSING
    session_time: str | None | MISSING = MISSING
    local_timers: BfdPeerTimer | None | MISSING = MISSING
    remote_timers: BfdPeerTimer | None | MISSING = MISSING


class GetEnterpriseBfdPeerStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    enterprise_id: int | None | MISSING = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    gateway_name: str | None | MISSING = MISSING
    gateway_logical_id: GatewayLogicalId | None | MISSING = MISSING
    gateway_state: GatewayState | None | MISSING = MISSING
    principle: str | None | MISSING = MISSING
    enterprise_object_id: int | None | MISSING = MISSING
    timestamp: VcoTimestamp | None | MISSING = MISSING
    tag: str | None | MISSING = MISSING
    data_key: str | None | MISSING = MISSING
    state: BfdPeerState | None | MISSING = MISSING
    data: BfdPeerStatusRecord | None | MISSING = MISSING


# --- getEnterpriseEdgeBgpPeerStatus ---


class GetEnterpriseEdgeBgpPeerStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING


class GetEnterpriseEdgeBgpPeerStatusResultItem(VcoModel):
    """A BGP peer state change event from the VCO monitoring API.

    The VCO returns time-series state change events, not a current peer table.
    BGP neighbor details are nested inside the ``data`` field.
    """

    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    timestamp: VcoTimestamp | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    enterprise_object_id: int | None | MISSING = MISSING
    tag: str | None | MISSING = MISSING
    data_key: str | None | MISSING = MISSING
    state: BgpPeerState | None | MISSING = MISSING
    data: BgpPeerData | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    edge_state: EdgeState | None | MISSING = MISSING


# --- getEnterpriseEdgeBfdPeerStatus ---


class GetEnterpriseEdgeBfdPeerStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = Field(
        default=MISSING, description="VCO Edge ID"
    )


class GetEnterpriseEdgeBfdPeerStatusResultItem(VcoModel):
    peer_address: str | None | MISSING = MISSING
    state: BfdPeerState | None | MISSING = MISSING
    local_discriminator: int | None | MISSING = MISSING
    remote_discriminator: int | None | MISSING = MISSING


# --- getEnterpriseEdgeClusterStatus ---


class GetEnterpriseEdgeClusterStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    role: HaRole | MISSING = MISSING
    metrics: list[HealthStatsMetric] | MISSING = MISSING


class ClusterEdgeEntry(VcoModel):
    """Health metrics for an edge member of a cluster."""

    model_config = ConfigDict(extra="allow")

    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_name: str | None | MISSING = MISSING
    tunnel_count: int | None | MISSING = MISSING
    memory_pct: float | None | MISSING = MISSING
    flow_count: int | None | MISSING = MISSING
    cpu_pct: float | None | MISSING = MISSING
    cpu_core_temp: float | None | MISSING = MISSING
    handoff_queue_drops: int | None | MISSING = MISSING
    tunnel_count_v6: int | None | MISSING = MISSING
    route_count: int | None | MISSING = MISSING
    route_count_v6: int | None | MISSING = MISSING
    nat_count: int | None | MISSING = MISSING
    flow_util_pct: float | None | MISSING = MISSING
    tunnel_util_pct: float | None | MISSING = MISSING


class GetEnterpriseEdgeClusterStatusResultItem(VcoModel):
    """Status of a single edge cluster within an enterprise."""

    model_config = ConfigDict(extra="allow")

    cluster_id: int | None | MISSING = MISSING
    cluster_name: str | None | MISSING = MISSING
    edges: list[ClusterEdgeEntry] | None | MISSING = MISSING


# --- getEnterpriseEdgeNvsTunnelStatus ---


class GetEnterpriseEdgeNvsTunnelStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    tag: NvsTunnelTag | MISSING = MISSING
    provider_object_id: int | MISSING = MISSING


class GetEnterpriseEdgeNvsTunnelStatusResultItemData(VcoModel):
    model_config = ConfigDict(extra="allow")

    name: str | None | MISSING = MISSING
    link: uuid.UUID | None | MISSING = MISSING
    nvs: uuid.UUID | None | MISSING = MISSING
    state: TunnelState | None | MISSING = MISSING
    local_interface_ip: str | None | MISSING = MISSING
    local_public_ip: str | None | MISSING = MISSING
    nvs_ip: str | None | MISSING = MISSING
    path_id: Annotated[str | None | MISSING, Alias("pathId")] = MISSING
    segment_id: Annotated[int | None | MISSING, Alias("segmentId")] = MISSING
    segment_logical_id: Annotated[
        uuid.UUID | None | MISSING, Alias("segmentLogicalId")
    ] = MISSING
    ike_id_type: Annotated[str | None | MISSING, Alias("ikeIdType")] = MISSING
    ike_id_value: Annotated[str | None | MISSING, Alias("ikeIdValue")] = MISSING
    tunneling_protocol: str | None | MISSING = MISSING


class GetEnterpriseEdgeNvsTunnelStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_object_id: int | None | MISSING = MISSING
    data: GetEnterpriseEdgeNvsTunnelStatusResultItemData | None | MISSING = MISSING
    data_key: str | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | None | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    state: TunnelState | None | MISSING = MISSING
    tag: str | None | MISSING = MISSING
    timestamp: VcoTimestamp | None | MISSING = MISSING


# --- getVPNEdgeActionStatus ---


class GetVpnEdgeActionStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    provider_object_id: int = Field(description="VPN provider object ID")
    fields_needed: list[str] | MISSING = MISSING
    limit: int | MISSING = MISSING


class GetVpnEdgeActionStatusResult(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    action_type: str | None | MISSING = MISSING
    state: str | None | MISSING = MISSING
    error: str | None | MISSING = MISSING


# --- getVPNSiteStatus ---


class GetVpnSiteStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    segment_object_id: Annotated[int, Resolvable(SegmentIdentity, "object_id")] = Field(
        description="Network segment object ID"
    )
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    provider_object_id: int = Field(description="VPN provider object ID")
    link_internal_id: str = Field(description="WAN link internal identifier")


class GetVpnSiteStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    action: str | None | MISSING = MISSING
    action_data: Any | None | MISSING = MISSING
    status: int | None | MISSING = MISSING
    status_msg: str | None | MISSING = MISSING
    vendor_type: str | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    provider_id: int | None | MISSING = MISSING
    # Retain the former public attributes for source compatibility. Current
    # VCO responses do not populate them; callers should use the fields above.
    site_id: int | None | MISSING = Field(
        default=MISSING, deprecated="Not returned by the current VCO contract."
    )
    site_name: str | None | MISSING = Field(
        default=MISSING, deprecated="Not returned by the current VCO contract."
    )
    state: str | None | MISSING = Field(
        default=MISSING, deprecated="Use status and statusMsg."
    )
    link_id: Annotated[
        int | None | MISSING,
        Resolvable(LinkIdentity, "id"),
    ] = Field(
        default=MISSING, deprecated="Use the request linkInternalId for link identity."
    )


# --- getAggregateEnterpriseEvents ---


class GetAggregateEnterpriseEventsFilter(VcoModel):
    limit: int | MISSING = MISSING


class GetAggregateEnterpriseEvents(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    enterprise_proxy_id: int | MISSING = MISSING
    with_: Annotated[
        list[GetAggregateEnterpriseEventsWith] | MISSING, Alias("with")
    ] = MISSING
    detail: bool | MISSING = MISSING
    filter: GetAggregateEnterpriseEventsFilter | MISSING = MISSING


class GetAggregateEnterpriseEventsResult(VcoModel):
    data: list[AggregateEventItem] | None | MISSING = MISSING
    meta_data: EventAggregateMetadata | None | MISSING = MISSING


# --- getAggregates ---


class GetAggregates(VcoModel):
    enterprise_id: int | MISSING = MISSING


class GetAggregatesResult(VcoModel):
    edge_count: int | None | MISSING = MISSING
    enterprises: list[dict[str, Any]] | None | MISSING = MISSING
    edges: dict[str, int] | None | MISSING = MISSING


# --- getEnterpriseAggregateEdgeNvsTunnelStatus ---


class GetEnterpriseAggregateEdgeNvsTunnelStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    provider_type: NvsProviderType = Field(description="NVS provider type filter")
    provider_object_id: int | MISSING = MISSING
    with_: Annotated[
        list[GetAggregateEdgeNvsTunnelStatusWith] | MISSING, Alias("with")
    ] = MISSING


class GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    name: str | None | MISSING = MISSING
    provider_type: str | None | MISSING = MISSING
    tunnel_state_counts: TunnelStateCounts | None | MISSING = MISSING
    data_centers: list[NvsDataCenter] | None | MISSING = MISSING


# --- getEnterpriseNniMetrics ---


class GetEnterpriseNniMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    metrics: list[NniMetric] | MISSING = MISSING


class GetEnterpriseNniMetricsResultItem(VcoModel):
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    gateway_name: str | None | MISSING = MISSING
    nni_id: int | None | MISSING = MISSING
    bytes_rx: int | None | MISSING = MISSING
    bytes_tx: int | None | MISSING = MISSING
    packets_rx: int | None | MISSING = MISSING
    packets_tx: int | None | MISSING = MISSING


# --- getEnterpriseNniSeries ---


class GetEnterpriseNniSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    metrics: list[NniMetric] | MISSING = MISSING
    sort: NniMetric | MISSING = MISSING
    limit: int | MISSING = MISSING
    nnis: list[NniIdentifier] | MISSING = MISSING


class GetEnterpriseNniSeriesResultItem(VcoModel):
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    gateway_name: str | None | MISSING = MISSING
    nni_id: int | None | MISSING = MISSING
    series: list[dict[str, Any]] | None | MISSING = MISSING


# --- getNetworkGatewayStatus ---


class GetNetworkGatewayStatus(VcoModel):
    enterprise_id: int | MISSING = MISSING
    network_id: int | MISSING = MISSING
    gateway_ids: list[int] | MISSING = MISSING
    time: VcoTimestamp | int | MISSING = Field(
        MISSING,
        description=(
            "Single absolute snapshot timestamp as ISO 8601, epoch milliseconds, "
            "or the VCO-supported HTTP date representation; omit for the latest "
            "available status. Not a duration or time interval."
        ),
        json_schema_extra={
            "anyOf": [
                {"type": "string", "format": "date-time"},
                {"type": "integer", "minimum": 1},
                {
                    "type": "string",
                    "pattern": (
                        r"^\w{3}\s\w{3}\s\d{2}\s\d{4}\s\d{2}:\d{2}:\d{2}"
                        r"\s\w{3}\+\d{4}\s\(\w+\)$"
                    ),
                },
            ]
        },
    )
    sort: GatewayStatusMetric | MISSING = MISSING
    limit: int | MISSING = MISSING
    metrics: list[GatewayStatusMetric] | MISSING = MISSING

    @field_validator("time", mode="before")
    @classmethod
    def _validate_epoch_milliseconds(cls, value: Any) -> Any:
        if isinstance(value, bool) or (isinstance(value, int) and value < 1):
            raise ValueError("epoch milliseconds must be at least 1")
        return value


class GetNetworkGatewayStatusResultItem(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    gateway_id: Annotated[int | None | MISSING, Resolvable(GatewayIdentity, "id")] = (
        MISSING
    )
    gateway_name: str | None | MISSING = MISSING
    gateway_state: GatewayState | None | MISSING = MISSING
    health_stats: GatewayHealthStats | None | MISSING = MISSING
    tunnel_count: int | None | MISSING = MISSING
    tunnel_count_v6: int | None | MISSING = MISSING
    memory_pct: float | None | MISSING = MISSING
    flow_count: int | None | MISSING = MISSING
    cpu_pct: float | None | MISSING = MISSING
    handoff_queue_drops: int | None | MISSING = MISSING
    connected_edges: float | None | MISSING = MISSING


# --- getWssLocationCreationStatus ---


class GetWssLocationCreationStatus(PaginatedVcoModel):
    enterprise_id: int | MISSING = MISSING
    integration_id: int = Field(description="WSS integration ID")
    limit: int | MISSING = MISSING
    quick_search: str | MISSING = MISSING


# --- get_enterprise_gateway_route_table_config ---


class GetEnterpriseGatewayRouteTableConfig(VcoModel):
    enterprise_id: int | MISSING = MISSING


class RouteTableGatewayEntry(VcoModel):
    """A gateway available for route table configuration."""

    name: str | None | MISSING = MISSING
    logical_id: str | None | MISSING = MISSING
    software_version: str | None | MISSING = MISSING
    principle: str | None | MISSING = MISSING


class RouteTableSegmentEntry(VcoModel):
    """A network segment in the route table configuration."""

    name: str | None | MISSING = MISSING
    segment_id: Annotated[
        int | None | MISSING, Resolvable(SegmentIdentity, "segment_id")
    ] = MISSING


class EnterpriseGatewayRouteTableConfigResult(VcoModel):
    """Gateway route table configuration showing available gateways and segments."""

    model_config = ConfigDict(extra="allow")

    gateways: list[RouteTableGatewayEntry] | None | MISSING = MISSING
    segments: list[RouteTableSegmentEntry] | None | MISSING = MISSING


# --- Link Insights: getLinkInsightsOverviewInfo ---


class LinkInsightsInterval(VcoModel):
    """Time interval accepted by Link Insights APIs."""

    start: VcoTimestamp
    end: VcoTimestamp


def _iter_api_filters(filters: ApiFilters) -> list[ApiFilter]:
    if isinstance(filters, ApiFilter):
        return [filters]
    if isinstance(filters, ApiFiltersAnd):
        return filters.and_
    if isinstance(filters, ApiFiltersOr):
        return filters.or_
    return []


class _LinkInsightsRequest(VcoModel):
    enterprise_id: int | MISSING = MISSING
    filters: ApiFilters | MISSING = MISSING
    filter_spec: Annotated[bool | MISSING, Alias("_filterSpec")] = MISSING

    _allowed_filter_fields: ClassVar[frozenset[str]] = (
        _LINK_INSIGHTS_METADATA_FILTER_FIELDS
    )

    @field_validator("filters")
    @classmethod
    def _require_supported_filter_fields(
        cls, value: ApiFilters | MISSING
    ) -> ApiFilters | MISSING:
        if value is MISSING:
            return value
        unsupported = {
            filter_.field
            for filter_ in _iter_api_filters(value)
            if filter_.field not in cls._allowed_filter_fields
        }
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported Link Insights filter fields: {fields}")
        return value


class GetLinkInsightsOverview(_LinkInsightsRequest):
    interval: LinkInsightsInterval


class LinkFlappingOverview(VcoModel):
    affected_links: int | None | MISSING = MISSING
    affected_edges: int | None | MISSING = MISSING
    affected_isps: int | None | MISSING = MISSING


class LinkDegradationOverview(VcoModel):
    degraded_links: int | None | MISSING = MISSING
    degraded_edges: int | None | MISSING = MISSING
    degraded_isps: int | None | MISSING = MISSING


class DegradationTime(VcoModel):
    total_minutes: float | None | MISSING = MISSING
    critical_minutes: float | None | MISSING = MISSING
    fair_minutes: float | None | MISSING = MISSING


class _LinkInsightsResult(VcoModel):
    """Common capability-discovery fields returned for ``_filterSpec`` requests."""

    pagination: dict[str, Any] | MISSING = MISSING
    sorting: dict[str, Any] | MISSING = MISSING
    quick_search: dict[str, Any] | MISSING = MISSING
    filtering: dict[str, Any] | MISSING = MISSING


class GetLinkInsightsOverviewResult(_LinkInsightsResult):
    total_links: int | None | MISSING = MISSING
    total_edges: int | None | MISSING = MISSING
    total_isps: int | None | MISSING = MISSING
    link_flapping: LinkFlappingOverview | None | MISSING = MISSING
    link_degradation: LinkDegradationOverview | None | MISSING = MISSING
    degradation_before: DegradationTime | None | MISSING = MISSING
    degradation_after: DegradationTime | None | MISSING = MISSING


# --- Link Insights: getLinkFlapSummary ---


class GetLinkFlapSummary(_LinkInsightsRequest):
    interval: LinkInsightsInterval
    view_by: DegradationViewBy
    limit: Annotated[int | MISSING, Field(ge=1, le=100)] = MISSING

    _allowed_filter_fields: ClassVar[frozenset[str]] = _LINK_FLAP_SUMMARY_FILTER_FIELDS


class LinkFlapSummaryDetail(VcoModel):
    num_links: int | None | MISSING = MISSING
    num_edges: int | None | MISSING = MISSING
    num_flapping_links: int | None | MISSING = MISSING
    num_flapping_edges: int | None | MISSING = MISSING


class LinkFlapSummaryItem(VcoModel):
    link_logical_id: str | None | MISSING = MISSING
    edge_logical_id: str | None | MISSING = MISSING
    link_name: str | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    link_ip_address: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    wan_link_type: str | None | MISSING = MISSING
    wan_link_mode: str | None | MISSING = MISSING
    total_flaps: int | None | MISSING = MISSING
    detail: LinkFlapSummaryDetail | None | MISSING = MISSING


class GetLinkFlapSummaryResult(_LinkInsightsResult):
    data: list[LinkFlapSummaryItem] | None | MISSING = MISSING


# --- Link Insights: getLinkDegradationSummary ---


class GetLinkDegradationSummary(_LinkInsightsRequest):
    interval: LinkInsightsInterval
    view_by: DegradationViewBy
    limit: Annotated[int | MISSING, Field(ge=1, le=100)] = MISSING
    with_: Annotated[
        list[DegradationWith] | MISSING, Field(max_length=2), Alias("with")
    ] = MISSING

    @field_validator("with_")
    @classmethod
    def _require_unique_with_values(
        cls, value: list[DegradationWith] | MISSING
    ) -> list[DegradationWith] | MISSING:
        if value is not MISSING and len(set(value)) != len(value):
            raise ValueError("with must contain unique values")
        return value


class DegradationSummaryDegradation(VcoModel):
    total_minutes: float | None | MISSING = MISSING
    critical_minutes: float | None | MISSING = MISSING
    fair_minutes: float | None | MISSING = MISSING
    before: DegradationTime | None | MISSING = MISSING
    after: DegradationTime | None | MISSING = MISSING


class DegradationSummaryItem(VcoModel):
    link_logical_id: str | None | MISSING = MISSING
    edge_logical_id: str | None | MISSING = MISSING
    link_name: str | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    link_ip_address: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    wan_link_type: str | None | MISSING = MISSING
    wan_link_mode: str | None | MISSING = MISSING
    detail: dict[str, Any] | None | MISSING = MISSING
    degradation: DegradationSummaryDegradation | None | MISSING = MISSING


class GetLinkDegradationSummaryResult(_LinkInsightsResult):
    data: list[DegradationSummaryItem] | None | MISSING = MISSING


# --- Link Insights: getLinkDegradationDetails ---


class DegradationDetailsSortItem(VcoModel):
    attribute: DegradationDetailsSortAttribute
    type_: Annotated[SortDirection, Alias("type")]


class GetLinkDegradationDetails(_LinkInsightsRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["interval"],
                    "not": {
                        "anyOf": [
                            {"required": ["nextPageLink"]},
                            {"required": ["prevPageLink"]},
                        ]
                    },
                },
                {
                    "required": ["nextPageLink"],
                    "not": {
                        "anyOf": [
                            {"required": ["interval"]},
                            {"required": ["prevPageLink"]},
                        ]
                    },
                },
                {
                    "required": ["prevPageLink"],
                    "not": {
                        "anyOf": [
                            {"required": ["interval"]},
                            {"required": ["nextPageLink"]},
                        ]
                    },
                },
            ]
        }
    )

    interval: LinkInsightsInterval | MISSING = MISSING
    limit: Annotated[int | MISSING, Field(ge=1, le=2048)] = MISSING
    sort_by: list[DegradationDetailsSortItem] | MISSING = MISSING
    quick_search: Annotated[str | MISSING, Field(min_length=1, max_length=255)] = (
        MISSING
    )
    next_page_link: Annotated[str | MISSING, Field(min_length=1)] = MISSING
    prev_page_link: Annotated[str | MISSING, Field(min_length=1)] = MISSING
    count: Annotated[bool | MISSING, Alias("_count")] = MISSING

    _allowed_filter_fields: ClassVar[frozenset[str]] = (
        _LINK_DEGRADATION_DETAIL_FILTER_FIELDS
    )

    @model_validator(mode="after")
    def _require_interval_or_single_page_link(self) -> Self:
        has_interval = self.interval is not MISSING
        page_link_count = sum(
            page_link is not MISSING
            for page_link in (self.next_page_link, self.prev_page_link)
        )
        if page_link_count > 1:
            raise ValueError("provide at most one of nextPageLink/prevPageLink")
        if has_interval == (page_link_count == 1):
            raise ValueError(
                "provide exactly one of interval, nextPageLink, or prevPageLink"
            )
        return self


class DegradationDetailDegradation(VcoModel):
    before: DegradationTime | None | MISSING = MISSING
    after: DegradationTime | None | MISSING = MISSING


class DegradationDetailItem(VcoModel):
    link_logical_id: str | None | MISSING = MISSING
    edge_logical_id: str | None | MISSING = MISSING
    link_name: str | None | MISSING = MISSING
    edge_name: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    link_ip_address: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    wan_link_type: str | None | MISSING = MISSING
    wan_link_mode: str | None | MISSING = MISSING
    traffic_type: str | None | MISSING = MISSING
    degradation: DegradationDetailDegradation | None | MISSING = MISSING
    packet_loss_rx: float | None | MISSING = MISSING
    jitter_rx: float | None | MISSING = MISSING
    latency_rx: float | None | MISSING = MISSING
    packet_loss_tx: float | None | MISSING = MISSING
    jitter_tx: float | None | MISSING = MISSING
    latency_tx: float | None | MISSING = MISSING
    seconds_up: int | None | MISSING = MISSING
    uptime_pct: float | None | MISSING = MISSING
    seconds_down: int | None | MISSING = MISSING
    seconds_unknown: int | None | MISSING = MISSING
    total_flaps: int | None | MISSING = MISSING
    current_state: LinkState | None | MISSING = MISSING
    current_state_since: VcoTimestamp | None | MISSING = MISSING
    last_updated: VcoTimestamp | None | MISSING = MISSING


class DegradationDetailsMetadata(VcoModel):
    more: bool | None | MISSING = MISSING
    limit: int | None | MISSING = MISSING
    next_page_link: str | None | MISSING = MISSING
    prev_page_link: str | None | MISSING = MISSING


class GetLinkDegradationDetailsResult(_LinkInsightsResult):
    data: list[DegradationDetailItem] | None | MISSING = MISSING
    meta_data: DegradationDetailsMetadata | None | MISSING = MISSING
    total_count: int | None | MISSING = MISSING
