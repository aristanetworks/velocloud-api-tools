import uuid
from typing import Annotated, Any

from pydantic import (
    MISSING,
    AliasChoices,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

from pyvelo.client.enums.edge import (
    EdgeState,
    HaRole,
    InterfaceState,
    LinkBackupState,
    LinkEffectiveState,
    LinkMode,
    LinkNetworkSide,
    LinkNetworkType,
    LinkServiceState,
    LinkState,
    LinkVpnState,
    SortDirection,
)
from pyvelo.client.enums.metrics import (
    AggregateEdgeLinkMetric,
    AppLinkMetric,
    AppMetric,
    AutoDualMode,
    BasicMetricViewBy,
    CategoryMetric,
    DestAttribute,
    DestMetric,
    DeviceMetric,
    EdgeTransportGroup,
    EnterpriseFirewallEdgeSummaryMetric,
    EnterpriseFirewallEdgeSummarySortMetric,
    EnterpriseFirewallIdpsMetric,
    EnterpriseFirewallIdpsViewBy,
    EnterpriseFirewallIdpsWith,
    EnterpriseFirewallMaliciousIpViewBy,
    EnterpriseFirewallMetricsWith,
    EnterpriseFirewallUrlCategoryViewBy,
    EnterpriseFirewallUrlReputationViewBy,
    FilterFieldOperator,
    FirewallIdpsMetric,
    FirewallIdpsSeriesViewBy,
    FirewallIdpsViewBy,
    FirewallMaliciousIpMetric,
    FirewallMaliciousIpViewBy,
    FirewallUrlCategoryMetric,
    FirewallUrlCategoryViewBy,
    FirewallUrlReputationMetric,
    FirewallUrlReputationViewBy,
    FlowFilterField,
    FlowVisibilityFilterField,
    FlowVisibilityMetric,
    FlowVisibilitySortMetric,
    GatewayStatusMetric,
    LinkMetric,
    NetworkInterfaceMetric,
    NsdTunnelStatsMetric,
    OsMetric,
    PathSeriesViewBy,
    PathStatus,
    SdwanPeerPathMetric,
    SegmentMetric,
    StatusMetric,
)
from pyvelo.client.models.configuration.common import AddressType
from pyvelo.client.schemas.monitoring import (
    GetEnterpriseEdgeLinkStatus,
    GetEnterpriseEdgeLinkStatusResultItem,
)
from pyvelo.core import Alias, ApiInterval, VcoModel, VcoTimestamp
from pyvelo.core.base import PaginatedVcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity, GatewayIdentity, LinkIdentity


class LinkPair(VcoModel):
    src_link_logical_id: uuid.UUID
    dest_link_logical_id: uuid.UUID | None = None

    @field_validator("dest_link_logical_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v


class GetEdgeSdwanPeerPathMetrics(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = MISSING
    peer_logical_id: Annotated[uuid.UUID, Resolvable(EdgeIdentity, "logical_id")] = (
        Field(description="SDWAN peer logical ID (UUID)")
    )
    interval: ApiInterval
    metrics: list[SdwanPeerPathMetric]


class GetEdgeSdwanPeerPathSeries(VcoModel):
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = MISSING
    peer_logical_id: Annotated[uuid.UUID, Resolvable(EdgeIdentity, "logical_id")] = (
        Field(description="SDWAN peer logical ID (UUID)")
    )
    paths: list[LinkPair] = Field(description="Link pairs for peer path queries")
    interval: ApiInterval
    metrics: list[SdwanPeerPathMetric]
    view_by: PathSeriesViewBy = Field(description="Dimension to group results by")
    max_samples: int | MISSING = MISSING


class SdwanEdgePeerPathLink(VcoModel):
    link_logical_id: uuid.UUID | None | MISSING = MISSING
    link_name: str | None | MISSING = MISSING
    device_logical_id: uuid.UUID | None | MISSING = MISSING
    device_name: str | None | MISSING = MISSING
    should_omit_standby: bool | MISSING = MISSING


class SdwanEdgePeerPathPair(VcoModel):
    source: SdwanEdgePeerPathLink
    destination: SdwanEdgePeerPathLink


class SdwanEdgePeerPathMetricsItem(SdwanEdgePeerPathPair):
    status: PathStatus
    path_type: AddressType | MISSING = MISSING
    ip_preference: AddressType
    metrics: dict[SdwanPeerPathMetric, float]


class PathTimeSeries(VcoModel):
    data: list[float | None]
    min_: Annotated[float | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | MISSING, Alias("max")] = MISSING
    metric: SdwanPeerPathMetric
    start_time: VcoTimestamp
    tick_interval: float
    total: float | MISSING = MISSING


class SdwanEdgePeerPathSeriesItem(SdwanEdgePeerPathPair):
    time_series: list[PathTimeSeries]


# Enterprise Flow Metrics types and models


class EnterpriseFlowMetricsFilter(VcoModel):
    field: FlowFilterField = Field(description="Metric field name to filter on")
    op: FilterFieldOperator = Field(description="Filter operator")
    value: Any | MISSING = MISSING


class GetEnterpriseFlowMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    view_by: BasicMetricViewBy
    metrics: list[DestMetric] | MISSING = MISSING
    sort: DestMetric | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING
    limit: int | MISSING = MISSING
    exclude_edge_hubs: bool | MISSING = MISSING


class EdgeAppMetrics(VcoModel):
    application: int | MISSING = MISSING
    name: int | str
    category: int | MISSING = MISSING
    category_name: str | MISSING = MISSING
    dest_fqdn: str | MISSING = MISSING
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    packets_rx: int
    packets_tx: int
    total_packets: int
    bytes_rx: int
    bytes_tx: int
    flow_count: int | MISSING = MISSING
    total_bytes: int
    service_groups: list[EdgeTransportGroup] | MISSING = MISSING


class EnterpriseAppOtherMetrics(VcoModel):
    application: int
    name: int | str
    category: int | MISSING = MISSING
    category_name: str | MISSING = MISSING
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    packets_rx: int
    packets_tx: int
    total_packets: int
    bytes_rx: int
    bytes_tx: int
    flow_count: int | MISSING = MISSING
    total_bytes: int


class EnterpriseFlowMetricsItem(VcoModel):

    application: int | MISSING = MISSING
    name: int | str
    category: int | MISSING = MISSING
    category_name: str | MISSING = MISSING
    dest_fqdn: str | MISSING = MISSING
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    service_groups: list[EdgeTransportGroup] | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING


# ---------------------------------------------------------------------------
# Edge App Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeAppMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    resolve_application_names: bool | MISSING = MISSING
    limit: int | MISSING = MISSING
    sort: AppMetric | MISSING = MISSING
    metrics: list[AppMetric] | MISSING = MISSING
    applications: list[str] | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeAppMetricsItem(VcoModel):
    name: int | str
    application: int | MISSING = MISSING
    category: int | MISSING = MISSING
    category_name: str | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Device Metrics request / response
# ---------------------------------------------------------------------------


class EdgeDeviceMetricsFilterRule(VcoModel):
    field: str = Field(description="Device metric field")
    op: str = Field(description="Filter operator")
    values: list[str] = Field(description="Values to match")


class EdgeDeviceMetricsFilter(VcoModel):
    rules: list[EdgeDeviceMetricsFilterRule] = Field(description="List of filter rules")


class GetEdgeDeviceMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    filter: Annotated[EdgeDeviceMetricsFilter | MISSING, Alias("filter")] = MISSING
    resolve_application_names: bool | MISSING = MISSING
    limit: int | MISSING = MISSING
    sort: DeviceMetric | MISSING = MISSING
    metrics: list[DeviceMetric] | MISSING = MISSING
    devices: list[str] | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeDeviceMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    device_mac: str | MISSING = MISSING
    device_ip: str | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Status Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeStatusMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    metrics: list[StatusMetric] | MISSING = MISSING
    role: HaRole | MISSING = MISSING


class EdgeStatusMetricsResult(VcoModel):
    tunnel_count: dict[str, float] | MISSING = MISSING
    cpu_pct: dict[str, float] | MISSING = MISSING
    memory_pct: dict[str, float] | MISSING = MISSING
    flow_count: dict[str, float] | MISSING = MISSING
    handoff_queue_drops: dict[str, float] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Health Stats (getEdgeStatusSeries with health data) request / response
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Edge Link Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeLinkMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: LinkMetric | MISSING = MISSING
    metrics: list[LinkMetric] | MISSING = MISSING
    links: list[int] | MISSING = MISSING


class EdgeLinkMetricsLink(VcoModel):
    id_: Annotated[int | None | MISSING, Alias("id")] = MISSING
    created: VcoTimestamp | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    logical_id: str | None | MISSING = MISSING
    internal_id: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    mac_address: str | None | MISSING = MISSING
    ip_address: str | None | MISSING = MISSING
    ip_v6_address: str | None | MISSING = MISSING
    netmask: str | None | MISSING = MISSING
    network_side: LinkNetworkSide | None | MISSING = MISSING
    network_type: LinkNetworkType | None | MISSING = MISSING
    display_name: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    org: str | None | MISSING = MISSING
    lat: float | None | MISSING = MISSING
    lon: float | None | MISSING = MISSING
    last_active: VcoTimestamp | None | MISSING = MISSING
    state: LinkState | None | MISSING = MISSING
    backup_state: LinkBackupState | None | MISSING = MISSING
    link_mode: LinkMode | None | MISSING = MISSING
    vpn_state: str | None | MISSING = MISSING
    last_event: VcoTimestamp | None | MISSING = MISSING
    last_event_state: LinkState | None | MISSING = MISSING
    alerts_enabled: int | None | MISSING = MISSING
    operator_alerts_enabled: int | None | MISSING = MISSING
    service_state: LinkServiceState | None | MISSING = MISSING
    modified: VcoTimestamp | None | MISSING = MISSING
    edge_to_edge_encryption: int | None | MISSING = MISSING
    effective_state: LinkEffectiveState | None | MISSING = MISSING


class EdgeLinkMetricsItem(VcoModel):
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    name: str | MISSING = MISSING
    link: EdgeLinkMetricsLink | MISSING = MISSING

    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING

    p1_bytes_rx: int | MISSING = MISSING
    p1_bytes_tx: int | MISSING = MISSING
    p1_packets_rx: int | MISSING = MISSING
    p1_packets_tx: int | MISSING = MISSING
    p2_bytes_rx: int | MISSING = MISSING
    p2_bytes_tx: int | MISSING = MISSING
    p2_packets_rx: int | MISSING = MISSING
    p2_packets_tx: int | MISSING = MISSING
    p3_bytes_rx: int | MISSING = MISSING
    p3_bytes_tx: int | MISSING = MISSING
    p3_packets_rx: int | MISSING = MISSING
    p3_packets_tx: int | MISSING = MISSING

    control_bytes_rx: int | MISSING = MISSING
    control_bytes_tx: int | MISSING = MISSING
    control_packets_rx: int | MISSING = MISSING
    control_packets_tx: int | MISSING = MISSING

    p1_encrypted_bytes_rx: int | MISSING = MISSING
    p1_encrypted_bytes_tx: int | MISSING = MISSING
    p1_encrypted_packets_rx: int | MISSING = MISSING
    p1_encrypted_packets_tx: int | MISSING = MISSING
    p2_encrypted_bytes_rx: int | MISSING = MISSING
    p2_encrypted_bytes_tx: int | MISSING = MISSING
    p2_encrypted_packets_rx: int | MISSING = MISSING
    p2_encrypted_packets_tx: int | MISSING = MISSING
    p3_encrypted_bytes_rx: int | MISSING = MISSING
    p3_encrypted_bytes_tx: int | MISSING = MISSING
    p3_encrypted_packets_rx: int | MISSING = MISSING
    p3_encrypted_packets_tx: int | MISSING = MISSING
    control_encrypted_bytes_rx: int | MISSING = MISSING
    control_encrypted_bytes_tx: int | MISSING = MISSING
    control_encrypted_packets_rx: int | MISSING = MISSING
    control_encrypted_packets_tx: int | MISSING = MISSING
    encrypted_bytes_rx: int | MISSING = MISSING
    encrypted_bytes_tx: int | MISSING = MISSING
    encrypted_packets_rx: int | MISSING = MISSING
    encrypted_packets_tx: int | MISSING = MISSING
    unencrypted_bytes_rx: int | MISSING = MISSING
    unencrypted_bytes_tx: int | MISSING = MISSING
    unencrypted_packets_rx: int | MISSING = MISSING
    unencrypted_packets_tx: int | MISSING = MISSING

    best_jitter_ms_rx: float | MISSING = MISSING
    best_jitter_ms_tx: float | MISSING = MISSING
    best_latency_ms_rx: float | MISSING = MISSING
    best_latency_ms_tx: float | MISSING = MISSING
    best_loss_pct_rx: float | MISSING = MISSING
    best_loss_pct_tx: float | MISSING = MISSING
    bps_of_best_path_rx: float | MISSING = MISSING
    bps_of_best_path_tx: float | MISSING = MISSING
    best_bw_kbps_rx: float | MISSING = MISSING
    best_bw_kbps_tx: float | MISSING = MISSING

    signal_strength: float | MISSING = MISSING
    score_tx: float | MISSING = MISSING
    score_rx: float | MISSING = MISSING
    auto_dual_mode: str | None | MISSING = MISSING
    max_interval_bps_rx: float | MISSING = MISSING
    max_interval_bps_tx: float | MISSING = MISSING
    min_interval_bps_rx: float | MISSING = MISSING
    min_interval_bps_tx: float | MISSING = MISSING

    @field_validator("auto_dual_mode", mode="before")
    @classmethod
    def _coerce_auto_dual_mode(cls, v: object) -> object:
        # VCO bug: null column is coerced to 0
        if v == 0:
            return None
        return v


# ---------------------------------------------------------------------------
# Edge Flow Visibility Metrics request / response
# ---------------------------------------------------------------------------


class FlowVisibilityFilter(VcoModel):
    """One ``api_filters`` condition for ``metrics/getEdgeFlowVisibilityMetrics``."""

    field: FlowVisibilityFilterField = Field(description="Flow attribute to filter on")
    operator: FilterFieldOperator = Field(
        validation_alias=AliasChoices("operator", "op"),
        description="Filter operator, e.g. is, isNot, contains, startsWith",
    )
    value: str | int | list[str | int] | MISSING = MISSING


class FlowVisibilityFilters(VcoModel):
    conditions: Annotated[list[FlowVisibilityFilter], Alias("and")] = Field(
        description="Array of filter conditions; each specifies field, operator and value"
    )


class FlowVisibilitySortSpec(VcoModel):
    attribute: FlowVisibilitySortMetric = Field(description="Metric field to sort by")
    type_: Annotated[SortDirection, Alias("type")] = Field(
        description="Sort direction: ASC or DESC"
    )


class GetEdgeFlowVisibilityMetrics(PaginatedVcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FlowVisibilityMetric | MISSING = Field(
        default=MISSING,
        description="Metric to sort results by (e.g. totalBytes); use sortOrder for direction",
    )
    sort_order: SortDirection | MISSING = Field(
        default=MISSING,
        description="Sort direction for the 'sort' metric: ASC or DESC",
    )
    metrics: list[FlowVisibilityMetric] | MISSING = MISSING
    quick_search: str | MISSING = MISSING
    filters: FlowVisibilityFilters | MISSING = MISSING
    sort_by: list[FlowVisibilitySortSpec] | MISSING = Field(
        default=MISSING,
        description=(
            "Sort by a display column (application, sourceIp, etc.)."
            " To sort by a metric like totalBytes, use 'sort' and 'sortOrder' instead."
            ' Example: [{"attribute": "application", "type": "DESC"}]'
        ),
    )


class FlowVisibilityItem(VcoModel):
    """One flow row from ``metrics/getEdgeFlowVisibilityMetrics``.

    VCO also appends a summary row (``name="other"`` with ``count``) to every
    page; ``get_edge_flow_visibility_metrics`` removes it.
    """

    application: int | str | None | MISSING = MISSING
    category: int | str | None | MISSING = MISSING
    source_ip: str | None | MISSING = MISSING
    host_name: str | None | MISSING = MISSING
    dest_ip: str | None | MISSING = MISSING
    dest_port: int | None | MISSING = MISSING
    transport: int | None | MISSING = MISSING
    dest_fqdn: Annotated[str | None | MISSING, Alias("destFQDN")] = MISSING
    dest_domain: str | None | MISSING = MISSING
    segment_id: int | None | MISSING = MISSING
    link_id: int | None | MISSING = MISSING
    link_name: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    next_hop: str | None | MISSING = MISSING
    route: str | int | None | MISSING = MISSING
    start_time: VcoTimestamp | None | MISSING = MISSING
    end_time: VcoTimestamp | None | MISSING = MISSING
    business_rule_name: str | None | MISSING = MISSING
    firewall_rule_name: str | None | MISSING = MISSING
    bytes_received: int | None | MISSING = MISSING
    bytes_sent: int | None | MISSING = MISSING
    packets_received: int | None | MISSING = MISSING
    packets_sent: int | None | MISSING = MISSING
    bytes_rx: int | None | MISSING = MISSING
    bytes_tx: int | None | MISSING = MISSING
    total_bytes: int | None | MISSING = MISSING
    packets_rx: int | None | MISSING = MISSING
    packets_tx: int | None | MISSING = MISSING
    total_packets: int | None | MISSING = MISSING
    flow_count: int | None | MISSING = MISSING
    name: str | None | MISSING = MISSING
    count: int | None | MISSING = MISSING

    def is_summary_row(self) -> bool:
        return self.name == "other" and self.count not in (MISSING, None)


# ---------------------------------------------------------------------------
# Edge Link Series request / response
# ---------------------------------------------------------------------------


class GetEdgeLinkSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: LinkMetric | MISSING = MISSING
    metrics: list[LinkMetric] | MISSING = MISSING
    links: list[int] | MISSING = MISSING


class LinkNumericTimeSeries(VcoModel):
    data: list[float]
    metric: LinkMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[float | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | MISSING, Alias("max")] = MISSING
    total: float | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, float("nan"))


def _coerce_none_in_series(values: Any, default: str | float) -> Any:
    if not isinstance(values, dict):
        return values
    data = values.get("data")
    if data is not None:
        values["data"] = [default if v is None else v for v in data]
    for field in ("min", "max", "total"):
        if field in values and values[field] is None:
            values[field] = default
    return values


class LinkStateTimeSeries(VcoModel):
    data: list[LinkState]
    metric: LinkMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[LinkState | MISSING, Alias("min")] = MISSING
    max_: Annotated[LinkState | MISSING, Alias("max")] = MISSING
    total: LinkState | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, LinkState.UNKNOWN)


class LinkAutoDualModeTimeSeries(VcoModel):
    data: list[AutoDualMode]
    metric: LinkMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[AutoDualMode | MISSING, Alias("min")] = MISSING
    max_: Annotated[AutoDualMode | MISSING, Alias("max")] = MISSING
    total: AutoDualMode | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, AutoDualMode.NA)


def _link_time_series_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        metric = v.get("metric", "")
    else:
        metric = getattr(v, "metric", "")
    if metric == LinkMetric.STATE:
        return "state"
    if metric == LinkMetric.AUTO_DUAL_MODE:
        return "auto_dual_mode"
    return "numeric"


type LinkTimeSeries = Annotated[
    Annotated[LinkNumericTimeSeries, Tag("numeric")]
    | Annotated[LinkStateTimeSeries, Tag("state")]
    | Annotated[LinkAutoDualModeTimeSeries, Tag("auto_dual_mode")],
    Discriminator(_link_time_series_discriminator),
]


class EdgeLinkSeriesItem(VcoModel):
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    link: EdgeLinkMetricsLink | MISSING = MISSING
    series: list[LinkTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Status Series request / response
# ---------------------------------------------------------------------------


class GetEdgeStatusSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    metrics: list[StatusMetric] | MISSING = MISSING
    role: HaRole | MISSING = MISSING


class EdgeStatusSeriesPoint(VcoModel):
    """One tick in a getEdgeStatusSeries response.

    Each item in the ``series`` array is a flat snapshot at a point in time,
    with metric values as top-level keys rather than the {metric, data[]}
    format used by getEdgeStatusSeries.
    """

    model_config = ConfigDict(extra="allow")

    start_time: VcoTimestamp
    cpu_pct: float | None | MISSING = MISSING
    memory_pct: float | None | MISSING = MISSING
    flow_count: float | None | MISSING = MISSING
    tunnel_count: float | None | MISSING = MISSING
    tunnel_count_v6: float | None | MISSING = MISSING
    handoff_queue_drops: float | None | MISSING = MISSING
    route_count: float | None | MISSING = MISSING
    route_count_v6: float | None | MISSING = MISSING
    nat_count: float | None | MISSING = MISSING
    flow_util_pct: float | None | MISSING = MISSING
    tunnel_util_pct: float | None | MISSING = MISSING
    cpu_core_temp: float | None | MISSING = MISSING


class EdgeStatusSeriesResult(VcoModel):
    series: list[EdgeStatusSeriesPoint] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge App Series request / response
# ---------------------------------------------------------------------------


class GetEdgeAppSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: AppMetric | MISSING = MISSING
    metrics: list[AppMetric] | MISSING = MISSING
    applications: list[int] | MISSING = MISSING
    max_samples: int | MISSING = MISSING
    resolve_application_names: bool | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class AppTimeSeries(VcoModel):
    data: list[float]
    metric: AppMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[float | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | MISSING, Alias("max")] = MISSING
    total: float | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, float("nan"))


class EdgeAppSeriesItem(VcoModel):
    name: int | str | MISSING = MISSING
    application: int | MISSING = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Category Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeCategoryMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: CategoryMetric | MISSING = MISSING
    metrics: list[CategoryMetric] | MISSING = MISSING
    categories: list[int] | MISSING = MISSING
    resolve_category_names: bool | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeCategoryMetricsItem(VcoModel):
    name: int | str | MISSING = MISSING
    category: int | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Dest Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeDestMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: DestMetric | MISSING = MISSING
    metrics: list[DestMetric] | MISSING = MISSING
    destinations: list[str] | MISSING = MISSING
    attribute: DestAttribute | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeDestMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    dest_domain: str | MISSING = MISSING
    dest_ip: str | MISSING = MISSING
    dest_fqdn: Annotated[str | MISSING, Alias("destFQDN")] = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Segment Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeSegmentMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: SegmentMetric | MISSING = MISSING
    metrics: list[SegmentMetric] | MISSING = MISSING
    segments: list[int] | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeSegmentMetricsItem(VcoModel):
    name: int | str | MISSING = MISSING
    segment: int | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge OS Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeOsMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: OsMetric | MISSING = MISSING
    metrics: list[OsMetric] | MISSING = MISSING
    os: Annotated[list[str] | MISSING, Alias("os")] = MISSING
    resolve_os_names: bool | MISSING = MISSING


class EdgeOsMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    os: Annotated[int | str | MISSING, Alias("os")] = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge OS Series request / response
# ---------------------------------------------------------------------------


class GetEdgeOsSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: OsMetric | MISSING = MISSING
    metrics: list[OsMetric] | MISSING = MISSING
    os: Annotated[list[str] | MISSING, Alias("os")] = MISSING
    resolve_os_names: bool | MISSING = MISSING
    max_samples: int | MISSING = MISSING


class EdgeOsSeriesItem(VcoModel):
    name: str | MISSING = MISSING
    os: Annotated[int | str | MISSING, Alias("os")] = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Network Interface Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeNetworkInterfaceMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    metrics: list[NetworkInterfaceMetric] | MISSING = MISSING
    role: HaRole | MISSING = MISSING


class EdgeNetworkInterfaceMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    interface: str | MISSING = MISSING
    bytes_in: int | MISSING = MISSING
    bytes_out: int | MISSING = MISSING
    packets_in: int | MISSING = MISSING
    packets_out: int | MISSING = MISSING
    errors_in: int | MISSING = MISSING
    errors_out: int | MISSING = MISSING
    drops_in: int | MISSING = MISSING
    drops_out: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Network Interface Series request / response
# ---------------------------------------------------------------------------


class GetEdgeNetworkInterfaceSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    metrics: list[NetworkInterfaceMetric] | MISSING = MISSING
    role: HaRole | MISSING = MISSING
    max_samples: int | MISSING = MISSING


class NetworkInterfaceNumericTimeSeries(VcoModel):
    data: list[float]
    metric: NetworkInterfaceMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[float | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | MISSING, Alias("max")] = MISSING
    total: float | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, float("nan"))


class NetworkInterfaceStateTimeSeries(VcoModel):
    data: list[InterfaceState]
    metric: NetworkInterfaceMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[InterfaceState | MISSING, Alias("min")] = MISSING
    max_: Annotated[InterfaceState | MISSING, Alias("max")] = MISSING
    total: InterfaceState | MISSING = MISSING

    @model_validator(mode="before")
    @classmethod
    def _coerce_none(cls, values: Any) -> Any:
        return _coerce_none_in_series(values, InterfaceState.UNKNOWN)


def _interface_time_series_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        metric = v.get("metric", "")
    else:
        metric = getattr(v, "metric", "")
    if metric == NetworkInterfaceMetric.STATE:
        return "state"
    return "numeric"


type NetworkInterfaceTimeSeries = Annotated[
    Annotated[NetworkInterfaceNumericTimeSeries, Tag("numeric")]
    | Annotated[NetworkInterfaceStateTimeSeries, Tag("state")],
    Discriminator(_interface_time_series_discriminator),
]


class EdgeNetworkInterfaceSeriesItem(VcoModel):
    name: str | MISSING = MISSING
    interface: str | MISSING = MISSING
    series: list[NetworkInterfaceTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge App Link Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeAppLinkMetrics(VcoModel):
    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    metrics: list[AppLinkMetric] | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeAppLinkMetricsItem(VcoModel):
    name: int | str | MISSING = MISSING
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    link_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(LinkIdentity, "internal_id")
    ] = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING
    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    flow_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge App Link Series request / response
# ---------------------------------------------------------------------------


class GetEdgeAppLinkSeries(VcoModel):
    id_: Annotated[int, Alias("id"), Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    metrics: list[AppLinkMetric] | MISSING = MISSING
    links: list[int] | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeAppLinkSeriesItem(VcoModel):
    name: int | str | MISSING = MISSING
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    link_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(LinkIdentity, "internal_id")
    ] = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Category Series request / response
# ---------------------------------------------------------------------------


class GetEdgeCategorySeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: CategoryMetric | MISSING = MISSING
    metrics: list[CategoryMetric] | MISSING = MISSING
    categories: list[int] | MISSING = MISSING
    resolve_category_names: bool | MISSING = MISSING
    max_samples: int | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeCategorySeriesItem(VcoModel):
    name: int | str | MISSING = MISSING
    category: int | MISSING = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Dest Series request / response
# ---------------------------------------------------------------------------


class GetEdgeDestSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: DestMetric | MISSING = MISSING
    destinations: list[str] | MISSING = MISSING
    attribute: DestAttribute | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeDestSeriesItem(VcoModel):
    name: str | MISSING = MISSING
    dest_domain: str | MISSING = MISSING
    dest_ip: str | MISSING = MISSING
    dest_fqdn: Annotated[str | MISSING, Alias("destFQDN")] = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Device Series request / response
# ---------------------------------------------------------------------------


class GetEdgeDeviceSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: DeviceMetric | MISSING = MISSING
    metrics: list[DeviceMetric] | MISSING = MISSING
    devices: list[str] | MISSING = MISSING
    max_samples: int | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeDeviceSeriesItem(VcoModel):
    name: str | MISSING = MISSING
    device_mac: str | MISSING = MISSING
    device_ip: str | MISSING = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Segment Series request / response
# ---------------------------------------------------------------------------


class GetEdgeSegmentSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: SegmentMetric | MISSING = MISSING
    metrics: list[SegmentMetric] | MISSING = MISSING
    segments: list[int] | MISSING = MISSING
    max_samples: int | MISSING = MISSING
    filters: list[EnterpriseFlowMetricsFilter] | MISSING = MISSING


class EdgeSegmentSeriesItem(VcoModel):
    name: int | str | MISSING = MISSING
    segment: int | MISSING = MISSING
    series: list[AppTimeSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Aggregate Edge Link Metrics request / response
# ---------------------------------------------------------------------------


class GetAggregateEdgeLinkMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval
    metrics: list[AggregateEdgeLinkMetric] | MISSING = MISSING
    enterprises: list[int] | MISSING = MISSING


class AggregateEdgeLinkMetricsLink(VcoModel):
    enterprise_name: str | None | MISSING = MISSING
    enterprise_id: int | None | MISSING = MISSING
    enterprise_proxy_id: int | None | MISSING = MISSING
    enterprise_proxy_name: str | None | MISSING = MISSING

    edge_name: str | None | MISSING = MISSING
    edge_id: Annotated[int | None | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    edge_serial_number: str | None | MISSING = MISSING
    edge_ha_serial_number: str | None | MISSING = MISSING
    edge_model_number: str | None | MISSING = MISSING
    edge_state: EdgeState | None | MISSING = MISSING
    edge_system_up_since: VcoTimestamp | None | MISSING = MISSING
    edge_service_up_since: VcoTimestamp | None | MISSING = MISSING
    edge_last_contact: VcoTimestamp | None | MISSING = MISSING
    edge_latitude: float | None | MISSING = MISSING
    edge_longitude: float | None | MISSING = MISSING

    display_name: str | None | MISSING = MISSING
    isp: str | None | MISSING = MISSING
    interface: str | None | MISSING = MISSING
    internal_id: str | None | MISSING = MISSING
    link_id: Annotated[int | None | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    link_ip_address: str | None | MISSING = MISSING
    link_ip_v6_address: str | None | MISSING = MISSING
    link_state: LinkState | None | MISSING = MISSING
    link_last_active: VcoTimestamp | None | MISSING = MISSING
    link_vpn_state: LinkVpnState | None | MISSING = MISSING
    link_mode: LinkMode | None | MISSING = MISSING
    link_backup_state: LinkBackupState | None | MISSING = MISSING
    edge_to_edge_encryption: int | None | MISSING = MISSING
    network_type: LinkNetworkType | None | MISSING = MISSING


class AggregateEdgeLinkMetricsItem(VcoModel):
    link_id: Annotated[int | MISSING, Resolvable(LinkIdentity, "id")] = MISSING
    name: str | MISSING = MISSING
    link: AggregateEdgeLinkMetricsLink | MISSING = MISSING

    bytes_rx: int | MISSING = MISSING
    bytes_tx: int | MISSING = MISSING
    total_bytes: int | MISSING = MISSING
    packets_rx: int | MISSING = MISSING
    packets_tx: int | MISSING = MISSING
    total_packets: int | MISSING = MISSING

    p1_bytes_rx: int | MISSING = MISSING
    p1_bytes_tx: int | MISSING = MISSING
    p1_packets_rx: int | MISSING = MISSING
    p1_packets_tx: int | MISSING = MISSING
    p2_bytes_rx: int | MISSING = MISSING
    p2_bytes_tx: int | MISSING = MISSING
    p2_packets_rx: int | MISSING = MISSING
    p2_packets_tx: int | MISSING = MISSING
    p3_bytes_rx: int | MISSING = MISSING
    p3_bytes_tx: int | MISSING = MISSING
    p3_packets_rx: int | MISSING = MISSING
    p3_packets_tx: int | MISSING = MISSING

    control_bytes_rx: int | MISSING = MISSING
    control_bytes_tx: int | MISSING = MISSING
    control_packets_rx: int | MISSING = MISSING
    control_packets_tx: int | MISSING = MISSING

    p1_encrypted_bytes_rx: int | MISSING = MISSING
    p1_encrypted_bytes_tx: int | MISSING = MISSING
    p1_encrypted_packets_rx: int | MISSING = MISSING
    p1_encrypted_packets_tx: int | MISSING = MISSING
    p2_encrypted_bytes_rx: int | MISSING = MISSING
    p2_encrypted_bytes_tx: int | MISSING = MISSING
    p2_encrypted_packets_rx: int | MISSING = MISSING
    p2_encrypted_packets_tx: int | MISSING = MISSING
    p3_encrypted_bytes_rx: int | MISSING = MISSING
    p3_encrypted_bytes_tx: int | MISSING = MISSING
    p3_encrypted_packets_rx: int | MISSING = MISSING
    p3_encrypted_packets_tx: int | MISSING = MISSING
    control_encrypted_bytes_rx: int | MISSING = MISSING
    control_encrypted_bytes_tx: int | MISSING = MISSING
    control_encrypted_packets_rx: int | MISSING = MISSING
    control_encrypted_packets_tx: int | MISSING = MISSING
    encrypted_bytes_rx: int | MISSING = MISSING
    encrypted_bytes_tx: int | MISSING = MISSING
    encrypted_packets_rx: int | MISSING = MISSING
    encrypted_packets_tx: int | MISSING = MISSING
    unencrypted_bytes_rx: int | MISSING = MISSING
    unencrypted_bytes_tx: int | MISSING = MISSING
    unencrypted_packets_rx: int | MISSING = MISSING
    unencrypted_packets_tx: int | MISSING = MISSING

    best_jitter_ms_rx: float | MISSING = MISSING
    best_jitter_ms_tx: float | MISSING = MISSING
    best_latency_ms_rx: float | MISSING = MISSING
    best_latency_ms_tx: float | MISSING = MISSING
    best_loss_pct_rx: float | MISSING = MISSING
    best_loss_pct_tx: float | MISSING = MISSING
    bps_of_best_path_rx: float | MISSING = MISSING
    bps_of_best_path_tx: float | MISSING = MISSING
    best_bw_kbps_rx: float | MISSING = MISSING
    best_bw_kbps_tx: float | MISSING = MISSING

    signal_strength: float | MISSING = MISSING
    score_tx: float | MISSING = MISSING
    score_rx: float | MISSING = MISSING
    auto_dual_mode: str | MISSING = MISSING


# ---------------------------------------------------------------------------
# Gateway Status Metrics request / response
# ---------------------------------------------------------------------------


class GetGatewayStatusMetrics(VcoModel):
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")] = Field(
        description="VCO gateway ID"
    )
    interval: ApiInterval
    metrics: list[GatewayStatusMetric] | MISSING = MISSING


class GatewayStatusMetricsResult(VcoModel):
    tunnel_count: dict[str, float] | MISSING = MISSING
    tunnel_count_v6: Annotated[dict[str, float] | MISSING, Alias("tunnelCountV6")] = (
        MISSING
    )
    cpu_pct: dict[str, float] | MISSING = MISSING
    memory_pct: dict[str, float] | MISSING = MISSING
    flow_count: dict[str, float] | MISSING = MISSING
    handoff_queue_drops: dict[str, float] | MISSING = MISSING
    connected_edges: dict[str, float] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Gateway Status Series request / response
# ---------------------------------------------------------------------------


class GetGatewayStatusSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    gateway_id: Annotated[int, Resolvable(GatewayIdentity, "id")] = Field(
        description="VCO gateway ID"
    )
    interval: ApiInterval
    metrics: list[GatewayStatusMetric] | MISSING = MISSING


class GatewayStatusSeriesItem(VcoModel):
    """Flat metric snapshot returned by gateway status series endpoints.

    Unlike HealthStatsTimeSeries (which has data/metric/tickInterval structure),
    gateway status series items are flat dicts with startTime and metric values
    like cpuPct, memoryPct, connectedEdges, etc.
    """

    model_config = ConfigDict(extra="allow")
    start_time: VcoTimestamp | MISSING = MISSING


class GatewayStatusSeriesResult(VcoModel):
    series: list[GatewayStatusSeriesItem] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Firewall IDPS Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeFirewallIdpsMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    view_by: FirewallIdpsViewBy = Field(description="Dimension to group results by")
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallIdpsMetric | MISSING = MISSING
    metrics: list[FirewallIdpsMetric] | MISSING = MISSING
    other: bool | MISSING = MISSING


class FirewallIdpsMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    threats_count: int | MISSING = MISSING
    critical_threats_count: int | MISSING = MISSING
    major_threats_count: int | MISSING = MISSING
    minor_threats_count: int | MISSING = MISSING
    signature_id: int | MISSING = MISSING
    signature_category: str | MISSING = MISSING
    threat_impact: str | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Firewall IDPS Series request / response
# ---------------------------------------------------------------------------


class GetEdgeFirewallIdpsSeries(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    view_by: FirewallIdpsSeriesViewBy
    max_samples: int
    interval: ApiInterval
    metrics: list[FirewallIdpsMetric] | MISSING = MISSING


class FirewallIdpsSeriesItem(VcoModel):
    name: str | MISSING = MISSING
    series: list[Any] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Firewall Malicious IP Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeFirewallMaliciousIpMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    view_by: FirewallMaliciousIpViewBy
    metrics: list[FirewallMaliciousIpMetric]
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallMaliciousIpMetric | MISSING = MISSING


class FirewallMaliciousIpMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    threats_count: int | MISSING = MISSING
    monitored_threats_count: int | MISSING = MISSING
    blocked_threats_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Firewall URL Category Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeFirewallUrlCategoryMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    view_by: FirewallUrlCategoryViewBy
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallUrlCategoryMetric | MISSING = MISSING
    metrics: list[FirewallUrlCategoryMetric] | MISSING = MISSING
    filters: list[dict[str, Any]] | MISSING = MISSING
    other: bool | MISSING = MISSING


class FirewallUrlCategoryMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    threats_count: int | MISSING = MISSING
    urls_blocked_count: int | MISSING = MISSING
    urls_monitored_count: int | MISSING = MISSING
    urls_allowed_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Firewall URL Reputation Metrics request / response
# ---------------------------------------------------------------------------


class GetEdgeFirewallUrlReputationMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    view_by: FirewallUrlReputationViewBy
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallUrlReputationMetric | MISSING = MISSING
    metrics: list[FirewallUrlReputationMetric] | MISSING = MISSING


class FirewallUrlReputationMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    threats_count: int | MISSING = MISSING
    urls_high_risk_count: int | MISSING = MISSING
    urls_medium_risk_count: int | MISSING = MISSING
    urls_low_risk_count: int | MISSING = MISSING
    urls_trustworthy_count: int | MISSING = MISSING
    urls_suspicious_count: int | MISSING = MISSING
    urls_blocked_count: int | MISSING = MISSING
    urls_monitored_count: int | MISSING = MISSING
    urls_allowed_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall IDPS Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallIdpsMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    view_by: EnterpriseFirewallIdpsViewBy
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: EnterpriseFirewallIdpsMetric | MISSING = MISSING
    metrics: list[EnterpriseFirewallIdpsMetric] | MISSING = MISSING
    other: bool | MISSING = MISSING
    with_: Annotated[list[EnterpriseFirewallIdpsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    edge_logical_ids: list[str] | MISSING = MISSING
    filters: list[dict[str, Any]] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall Edge Count Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallEdgeCountMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    interval: ApiInterval


class EnterpriseFirewallEdgeCountMetricsResult(VcoModel):
    total_edges_count: int | MISSING = MISSING
    impacted_edges_count: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall Edge Summary Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallEdgeSummaryMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    metrics: list[EnterpriseFirewallEdgeSummaryMetric]
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: EnterpriseFirewallEdgeSummarySortMetric | MISSING = MISSING
    edge_logical_ids: list[str] | MISSING = MISSING


class FirewallEdgeSummaryMetricsItem(VcoModel):
    name: str | MISSING = MISSING
    edge_logical_id: Annotated[
        uuid.UUID | MISSING, Resolvable(EdgeIdentity, "logical_id")
    ] = MISSING
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    idps_actions: dict[str, Any] | MISSING = MISSING
    url_cat_actions: dict[str, Any] | MISSING = MISSING
    url_rep_actions: dict[str, Any] | MISSING = MISSING
    mal_ip_actions: dict[str, Any] | MISSING = MISSING
    total_actions: dict[str, Any] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall Malicious IP Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallMaliciousIpMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    view_by: EnterpriseFirewallMaliciousIpViewBy
    metrics: list[FirewallMaliciousIpMetric]
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallMaliciousIpMetric | MISSING = MISSING
    with_: Annotated[list[EnterpriseFirewallMetricsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    edge_logical_ids: list[str] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall URL Category Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallUrlCategoryMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    view_by: EnterpriseFirewallUrlCategoryViewBy
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallUrlCategoryMetric | MISSING = MISSING
    metrics: list[FirewallUrlCategoryMetric] | MISSING = MISSING
    other: bool | MISSING = MISSING
    with_: Annotated[list[EnterpriseFirewallMetricsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    edge_logical_ids: list[str] | MISSING = MISSING
    filters: list[dict[str, Any]] | MISSING = MISSING


# ---------------------------------------------------------------------------
# Enterprise Firewall URL Reputation Metrics request / response
# ---------------------------------------------------------------------------


class GetEnterpriseFirewallUrlReputationMetrics(VcoModel):
    enterprise_id: int | MISSING = MISSING
    view_by: EnterpriseFirewallUrlReputationViewBy
    interval: ApiInterval
    limit: int | MISSING = MISSING
    sort: FirewallUrlReputationMetric | MISSING = MISSING
    metrics: list[FirewallUrlReputationMetric] | MISSING = MISSING
    with_: Annotated[list[EnterpriseFirewallMetricsWith] | MISSING, Alias("with")] = (
        MISSING
    )
    edge_logical_ids: list[str] | MISSING = MISSING


# ---------------------------------------------------------------------------
# NSD Tunnel Stats request / response
# ---------------------------------------------------------------------------


class GetNsdTunnelStats(VcoModel):
    enterprise_id: int | MISSING = MISSING
    nsd_logical_id: uuid.UUID = Field(description="NSD tunnel logical ID (UUID)")
    interval: ApiInterval
    metrics: list[NsdTunnelStatsMetric] | MISSING = MISSING


class NsdTunnelStatsSeries(VcoModel):
    data: list[float | None]
    metric: NsdTunnelStatsMetric
    start_time: VcoTimestamp
    tick_interval: float
    min_: Annotated[float | None | MISSING, Alias("min")] = MISSING
    max_: Annotated[float | None | MISSING, Alias("max")] = MISSING
    total: float | None | MISSING = MISSING


class NsdTunnelStatsResultItem(VcoModel):
    dc_public_ip: str | MISSING = MISSING
    series: list[NsdTunnelStatsSeries] | MISSING = MISSING


# ---------------------------------------------------------------------------
# NSD Edge Tunnel Stats request / response
# ---------------------------------------------------------------------------


class GetNsdEdgeTunnelStats(VcoModel):
    enterprise_id: int | MISSING = MISSING
    nsd_logical_id: uuid.UUID = Field(description="NSD tunnel logical ID (UUID)")
    edge_id: Annotated[int, Resolvable(EdgeIdentity, "id")] = Field(
        description="VCO Edge ID"
    )
    interval: ApiInterval
    metrics: list[NsdTunnelStatsMetric] | MISSING = MISSING


class NsdEdgeTunnelStatsResultItem(VcoModel):
    nvs_public_ip: str | MISSING = MISSING
    series: list[NsdTunnelStatsSeries] | MISSING = MISSING


__all__ = [
    "AggregateEdgeLinkMetricsItem",
    "AggregateEdgeLinkMetricsLink",
    "AppTimeSeries",
    "EdgeAppLinkMetricsItem",
    "EdgeAppLinkSeriesItem",
    "EdgeAppMetrics",
    "EdgeAppMetricsItem",
    "EdgeAppSeriesItem",
    "EdgeCategoryMetricsItem",
    "EdgeCategorySeriesItem",
    "EdgeDestMetricsItem",
    "EdgeDestSeriesItem",
    "EdgeDeviceMetricsFilter",
    "EdgeDeviceMetricsFilterRule",
    "EdgeDeviceMetricsItem",
    "EdgeDeviceSeriesItem",
    "EdgeLinkMetricsItem",
    "EdgeLinkMetricsLink",
    "EdgeLinkSeriesItem",
    "EdgeNetworkInterfaceMetricsItem",
    "EdgeNetworkInterfaceSeriesItem",
    "EdgeOsMetricsItem",
    "EdgeOsSeriesItem",
    "EdgeSegmentMetricsItem",
    "EdgeSegmentSeriesItem",
    "EdgeStatusMetricsResult",
    "EdgeStatusSeriesPoint",
    "EdgeStatusSeriesResult",
    "EnterpriseAppOtherMetrics",
    "EnterpriseFirewallEdgeCountMetricsResult",
    "EnterpriseFlowMetricsFilter",
    "EnterpriseFlowMetricsItem",
    "FirewallEdgeSummaryMetricsItem",
    "FirewallIdpsMetricsItem",
    "FirewallIdpsSeriesItem",
    "FirewallMaliciousIpMetricsItem",
    "FirewallUrlCategoryMetricsItem",
    "FirewallUrlReputationMetricsItem",
    "FlowVisibilityFilter",
    "FlowVisibilityFilters",
    "FlowVisibilityItem",
    "FlowVisibilitySortSpec",
    "GatewayStatusMetricsResult",
    "GatewayStatusSeriesItem",
    "GatewayStatusSeriesResult",
    "GetAggregateEdgeLinkMetrics",
    "GetEdgeAppLinkMetrics",
    "GetEdgeAppLinkSeries",
    "GetEdgeAppMetrics",
    "GetEdgeAppSeries",
    "GetEdgeCategoryMetrics",
    "GetEdgeCategorySeries",
    "GetEdgeDestMetrics",
    "GetEdgeDestSeries",
    "GetEdgeDeviceMetrics",
    "GetEdgeDeviceSeries",
    "GetEdgeFirewallIdpsMetrics",
    "GetEdgeFirewallIdpsSeries",
    "GetEdgeFirewallMaliciousIpMetrics",
    "GetEdgeFirewallUrlCategoryMetrics",
    "GetEdgeFirewallUrlReputationMetrics",
    "GetEdgeFlowVisibilityMetrics",
    "GetEdgeLinkMetrics",
    "GetEdgeLinkSeries",
    "GetEdgeNetworkInterfaceMetrics",
    "GetEdgeNetworkInterfaceSeries",
    "GetEdgeOsMetrics",
    "GetEdgeOsSeries",
    "GetEdgeSdwanPeerPathMetrics",
    "GetEdgeSdwanPeerPathSeries",
    "GetEdgeSegmentMetrics",
    "GetEdgeSegmentSeries",
    "GetEdgeStatusMetrics",
    "GetEdgeStatusSeries",
    "GetEnterpriseEdgeLinkStatus",
    "GetEnterpriseEdgeLinkStatusResultItem",
    "GetEnterpriseFirewallEdgeCountMetrics",
    "GetEnterpriseFirewallEdgeSummaryMetrics",
    "GetEnterpriseFirewallIdpsMetrics",
    "GetEnterpriseFirewallMaliciousIpMetrics",
    "GetEnterpriseFirewallUrlCategoryMetrics",
    "GetEnterpriseFirewallUrlReputationMetrics",
    "GetEnterpriseFlowMetrics",
    "GetGatewayStatusMetrics",
    "GetGatewayStatusSeries",
    "GetNsdEdgeTunnelStats",
    "GetNsdTunnelStats",
    "LinkAutoDualModeTimeSeries",
    "LinkNumericTimeSeries",
    "LinkPair",
    "LinkStateTimeSeries",
    "LinkTimeSeries",
    "NsdEdgeTunnelStatsResultItem",
    "NsdTunnelStatsResultItem",
    "NsdTunnelStatsSeries",
    "PathTimeSeries",
    "SdwanEdgePeerPathLink",
    "SdwanEdgePeerPathMetricsItem",
    "SdwanEdgePeerPathPair",
    "SdwanEdgePeerPathSeriesItem",
]
