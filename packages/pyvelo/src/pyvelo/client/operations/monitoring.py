from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import RootModel, ValidationError

from pyvelo.client.operations._tagging import EVENTS, PM_HEALTH, PM_QOE, domain_tag
from pyvelo.client.schemas.link_quality import (
    GetLinkQualityEvents,
    GetLinkQualityEventsResult,
)
from pyvelo.client.schemas.metrics import (
    AggregateEdgeLinkMetricsItem,
    GetAggregateEdgeLinkMetrics,
)
from pyvelo.client.schemas.monitoring import (
    EnterpriseGatewayRouteTableConfigResult,
    GetAggregateEnterpriseEvents,
    GetAggregateEnterpriseEventsResult,
    GetAggregates,
    GetAggregatesResult,
    GetEnterpriseAggregateEdgeNvsTunnelStatus,
    GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem,
    GetEnterpriseBfdPeerStatus,
    GetEnterpriseBfdPeerStatusResultItem,
    GetEnterpriseBgpPeerStatus,
    GetEnterpriseBgpPeerStatusResultItem,
    GetEnterpriseEdgeBfdPeerStatus,
    GetEnterpriseEdgeBfdPeerStatusResultItem,
    GetEnterpriseEdgeBgpPeerStatus,
    GetEnterpriseEdgeBgpPeerStatusResultItem,
    GetEnterpriseEdgeClusterStatus,
    GetEnterpriseEdgeClusterStatusResultItem,
    GetEnterpriseEdgeLinkStatus,
    GetEnterpriseEdgeLinkStatusResultItem,
    GetEnterpriseEdgeNvsTunnelStatus,
    GetEnterpriseEdgeNvsTunnelStatusResultItem,
    GetEnterpriseEdgeStatus,
    GetEnterpriseEdgeStatusResultItem,
    GetEnterpriseGatewayRouteTableConfig,
    GetEnterpriseNniMetrics,
    GetEnterpriseNniMetricsResultItem,
    GetEnterpriseNniSeries,
    GetEnterpriseNniSeriesResultItem,
    GetLinkDegradationDetails,
    GetLinkDegradationDetailsResult,
    GetLinkDegradationSummary,
    GetLinkDegradationSummaryResult,
    GetLinkFlapSummary,
    GetLinkFlapSummaryResult,
    GetLinkInsightsOverview,
    GetLinkInsightsOverviewResult,
    GetNetworkGatewayStatus,
    GetNetworkGatewayStatusResultItem,
    GetVpnEdgeActionStatus,
    GetVpnEdgeActionStatusResult,
    GetVpnSiteStatus,
    GetVpnSiteStatusResultItem,
    GetWssLocationCreationStatus,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class MonitoringMixin(Protocol):
    """Operations for monitoring/ and linkQualityEvent/ API endpoints."""

    # --- monitoring/ endpoints ---

    @domain_tag(PM_QOE)
    async def get_enterprise_edge_link_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeLinkStatus
    ) -> Sequence[GetEnterpriseEdgeLinkStatusResultItem]:
        """Retrieve current WAN link status for all Edges across one or more enterprises.

        Returns the real-time operational state of every WAN link on every Edge in the
        enterprise, including link connectivity state, IP addressing, ISP information,
        and last-active timestamps. In VeloCloud SD-WAN, each Edge can have multiple WAN
        links (public internet, MPLS, LTE) that are continuously monitored and used for
        dynamic path selection. Useful for building link health dashboards, identifying
        failed or degraded links across the fleet, and correlating link status with
        overlay path quality.

        Returns:
            Array of Edge link status entries with per-link connectivity details
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeLinkStatus",
            request,
            GetEnterpriseEdgeLinkStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_edge_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeStatus
    ) -> Sequence[GetEnterpriseEdgeStatusResultItem]:
        """Get Edge monitoring status with aggregate health metric summaries for an enterprise.

        Retrieves aggregate healthStats metric summaries for the specified enterprise and its
        Edges over a specified time interval. In the VeloCloud SD-WAN architecture, Edges are
        the CPE (Customer Premises Equipment) appliances deployed at branch sites that form
        the SD-WAN overlay network. This endpoint provides operational health data including
        Edge connectivity state (CONNECTED, OFFLINE, DEGRADED), CPU and memory utilization,
        tunnel counts, flow counts, and handoff queue drops. Use this to get a comprehensive
        overview of Edge fleet health across the enterprise and to quickly identify Edges that
        may be experiencing issues.

        Returns:
            Array of healthStats objects for all requested Edges and metrics, each containing
            Edge identification info, operational state, and aggregate health metric summaries
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeStatus",
            request,
            GetEnterpriseEdgeStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_bgp_peer_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseBgpPeerStatus
    ) -> Sequence[GetEnterpriseBgpPeerStatusResultItem]:
        """Get gateway BGP peer status for all gateways serving the enterprise.

        Returns one entry per gateway BGP session. Each row is a
        ``gatewayEnterpriseRecord`` with tag ``BGP_NEIGHBOR_SUMMARY``, keyed by
        neighbor IP. The nested ``data`` object contains BGP session details
        (neighbor IP/AS, message counts, prefix counts, uptime, segment).
        """
        return await self.portal_list(
            "monitoring/getEnterpriseBgpPeerStatus",
            request,
            GetEnterpriseBgpPeerStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_bfd_peer_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseBfdPeerStatus
    ) -> Sequence[GetEnterpriseBfdPeerStatusResultItem]:
        """Get gateway BFD peer status for all gateways serving the enterprise.

        Returns one entry per gateway BFD session. Each row is a
        ``gatewayEnterpriseRecord`` with tag ``BFD_NEIGHBOR_SUMMARY``, keyed by
        peer address. The nested ``data`` object contains BFD session details
        (peer/local addresses, timers, session time, segment).
        """
        return await self.portal_list(
            "monitoring/getEnterpriseBfdPeerStatus",
            request,
            GetEnterpriseBfdPeerStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_edge_bgp_peer_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeBgpPeerStatus
    ) -> Sequence[GetEnterpriseEdgeBgpPeerStatusResultItem]:
        """Get edge BGP peer state change events.

        Returns time-series state change events, not a current peer table. The
        same neighbor appears multiple times (once per state transition). To get
        current state, group by ``data_key`` (neighbor IP) and take the highest
        ``id_``. Both ``enterprise_id`` and ``edge_id`` are optional; omit
        ``edge_id`` to get events for all edges.

        BGP details are in the nested ``data`` object. Counter fields
        (``msg_rcvd``, ``msg_sent``, ``pfx_rcvd``) are ints; ``neighbor_as``
        stays a string (ASNs may be dotted, e.g. "65002.65530").
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeBgpPeerStatus",
            request,
            GetEnterpriseEdgeBgpPeerStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_edge_bfd_peer_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeBfdPeerStatus
    ) -> Sequence[GetEnterpriseEdgeBfdPeerStatusResultItem]:
        """Get Edge BFD peer status for a specific Edge in the enterprise.

        Retrieves the BFD (Bidirectional Forwarding Detection) peer status for the specified
        Edge. BFD is a lightweight protocol used in VeloCloud SD-WAN to rapidly detect failures
        in the forwarding path between an Edge and its adjacent routing peers (typically local
        routers or Layer 3 switches). BFD sessions provide sub-second failure detection, which
        is much faster than relying on BGP hold timers alone. Each entry in the response
        represents a BFD session on the Edge and includes the peer address, session state
        (Up, Down, Init, AdminDown), local/remote discriminators, and detection parameters.
        Use this to verify that fast-failover detection is operational at a specific site or
        to troubleshoot convergence issues.

        Returns:
            Array where each entry represents an Edge BFD session with peer address,
            session state (Up/Down/Init/AdminDown), discriminator values, and detection
            timing parameters
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeBfdPeerStatus",
            request,
            GetEnterpriseEdgeBfdPeerStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_edge_cluster_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeClusterStatus
    ) -> Sequence[GetEnterpriseEdgeClusterStatusResultItem]:
        """Get Edge cluster status and utilization data by cluster for the enterprise.

        Retrieves the High Availability (HA) cluster status for Edges in the enterprise. In
        VeloCloud SD-WAN, Edge clusters allow multiple Edges at a single site to operate as
        a group for load balancing and redundancy. Clusters provide active-active or
        active-standby configurations where traffic is distributed across cluster members.
        This endpoint returns utilization data organized by cluster, including each cluster
        member's role (hub, spoke), operational state, and resource utilization metrics. Use
        this to monitor cluster health, verify that HA failover configurations are functioning
        correctly, and identify capacity issues in clustered deployments.

        Returns:
            Array of cluster status objects, each containing cluster identification,
            member Edge states, roles, and utilization data
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeClusterStatus",
            request,
            GetEnterpriseEdgeClusterStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_edge_nvs_tunnel_status(
        self: VcoClientBaseProtocol, request: GetEnterpriseEdgeNvsTunnelStatus
    ) -> Sequence[GetEnterpriseEdgeNvsTunnelStatusResultItem]:
        """Get state history for tunnels from an Edge to non-SD-WAN sites (NVS).

        Retrieves the tunnel state change history for IPsec/GRE tunnels from the specified Edge
        to non-SD-WAN destinations (also called Non-VeloCloud Sites or NVS). In VeloCloud
        SD-WAN, non-SD-WAN destinations include data centers, cloud security services (e.g.,
        Zscaler, Palo Alto Prisma), and other third-party sites that are not part of the
        VeloCloud overlay but need to be connected via IPsec or GRE tunnels directly from
        the Edge. Each entry represents an Edge-reported state change in a tunnel, and tunnels
        are uniquely identified by a dataKey corresponding to an Edge-generated pathId. Use
        this to troubleshoot connectivity to cloud security services, third-party VPNs, or
        data center tunnels originating from a specific Edge.

        Returns:
            Array where each entry represents a tunnel state change event, including the
            tunnel dataKey (pathId), destination info, tunnel type (IPsec/GRE), state
            (UP/DOWN), and timestamp of the state change
        """
        return await self.portal_list(
            "monitoring/getEnterpriseEdgeNvsTunnelStatus",
            request,
            GetEnterpriseEdgeNvsTunnelStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_vpn_edge_action_status(
        self: VcoClientBaseProtocol, request: GetVpnEdgeActionStatus
    ) -> GetVpnEdgeActionStatusResult:
        """Get the VPN edge action status for automated VPN deployments.

        Retrieves the VPN edge action status for automated SD-WAN VPN deployments on a specific
        Edge. In VeloCloud SD-WAN, VPN tunnels between Edges and to non-SD-WAN sites can be
        provisioned automatically through the Orchestrator. This endpoint returns the status of
        the most recent VPN-related actions (e.g., tunnel creation, modification, teardown)
        that have been initiated or completed on the specified Edge. This includes the action
        type, current state (pending, in progress, completed, failed), and any error details.
        Use this to monitor the progress of automated VPN provisioning or to troubleshoot
        failed VPN tunnel deployments.

        Returns:
            Object containing the most recent VPN edge action records with action type,
            current state (pending/in-progress/completed/failed), timestamps, and error
            details if applicable
        """
        return await self.portal(
            "monitoring/getVPNEdgeActionStatus",
            request,
            GetVpnEdgeActionStatusResult,
        )

    @domain_tag(PM_HEALTH)
    async def get_vpn_site_status(
        self: VcoClientBaseProtocol, request: GetVpnSiteStatus
    ) -> Sequence[GetVpnSiteStatusResultItem]:
        """Get the latest automated Non-SD-WAN Destination deployment status.

        Retrieves the latest deployment action matching one Edge, segment, provider, and
        WAN link. This endpoint reports the automation action's status; it is not a general
        VPN tunnel-health or enterprise-wide VPN inventory API.

        Returns:
            Matching deployment action records, including the action ID/type, numeric status,
            human-readable status message, vendor type, and available Edge/provider IDs.
        """
        return await self.portal_list(
            "monitoring/getVPNSiteStatus",
            request,
            GetVpnSiteStatusResultItem,
        )

    @domain_tag(EVENTS)
    async def get_aggregate_enterprise_events(
        self: VcoClientBaseProtocol, request: GetAggregateEnterpriseEvents
    ) -> GetAggregateEnterpriseEventsResult:
        """Get events across all enterprises in a paginated list.

        Retrieves events spanning all enterprises managed under the caller's context. When
        called in an MSP/Partner context, queries only enterprises managed by that partner.
        In VeloCloud SD-WAN, this provides a unified, cross-enterprise view of events such
        as Edge state changes, link failures, HA failovers, configuration updates, and alerts.
        This is particularly useful for operators or MSPs who need to monitor event activity
        across their entire managed customer base from a single query, rather than querying
        each enterprise individually. Results are returned in a paginated list within the
        specified time interval.

        Returns:
            Paginated list of events across all enterprises, each containing event type,
            severity, message, timestamp, and the associated enterprise and Edge identifiers
        """
        return await self.portal(
            "monitoring/getAggregateEnterpriseEvents",
            request,
            GetAggregateEnterpriseEventsResult,
        )

    @domain_tag(PM_HEALTH)
    async def get_aggregates(
        self: VcoClientBaseProtocol, request: GetAggregates
    ) -> GetAggregatesResult:
        """Get aggregate enterprise and Edge information for the network.

        Returns a comprehensive listing of all enterprises and Edges on the network. The
        response includes an aggregate edgeCount, a list of enterprise objects (with details
        such as enterprise name, ID, and configuration), and a map that provides Edge counts
        per enterprise. In VeloCloud SD-WAN, this gives operators and MSPs a high-level
        inventory view of the entire managed network, including how many Edges are deployed
        per customer enterprise. Use this for fleet management, capacity planning, or to get
        a quick summary of the network's scale and structure.

        Returns:
            Object containing an aggregate "edgeCount", an "enterprises" list of enterprise
            objects with their details, and an "edges" map providing Edge counts keyed by
            enterprise ID
        """
        return await self.portal(
            "monitoring/getAggregates",
            request,
            GetAggregatesResult,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_aggregate_edge_nvs_tunnel_status(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseAggregateEdgeNvsTunnelStatus,
    ) -> Sequence[GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem]:
        """Get aggregate tunnel state counts and data centers for non-SD-WAN-via-Edge network services.

        Returns a summary of tunnel connectivity status for all non-SD-WAN-via-Edge network
        services (e.g., Zscaler Cloud Security Service, Palo Alto Prisma Access, generic IKEv2
        IPsec endpoints) across the enterprise. Each entry in the response represents a
        non-SD-WAN network service and includes an aggregate tunnel state count object (showing
        how many tunnels are up, down, or in other states) and an array of unique data centers
        that Edges connect to under that service. In VeloCloud SD-WAN, non-SD-WAN-via-Edge
        services allow branch Edges to establish direct IPsec tunnels to third-party cloud
        security or data center endpoints without routing through a Gateway. Use this to
        monitor the overall health of cloud security service connectivity and to identify
        services with degraded tunnel availability.

        Returns:
            Array where each entry represents a non-SD-WAN-via-Edge network service with
            aggregate tunnel state counts (up/down/unknown), list of data centers, and
            service identification information
        """
        return await self.portal_list(
            "monitoring/getEnterpriseAggregateEdgeNvsTunnelStatus",
            request,
            GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_nni_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseNniMetrics
    ) -> Sequence[GetEnterpriseNniMetricsResultItem]:
        """Get partner gateway NNI (Network-to-Network Interface) metrics for the enterprise.

        Retrieves NNI metrics for partner gateways serving the specified enterprise. In
        VeloCloud SD-WAN, NNI (Network-to-Network Interface) connections exist between
        VeloCloud Partner Gateways and the partner's or customer's network infrastructure.
        These are the hand-off points where SD-WAN traffic is exchanged with traditional
        network infrastructure (e.g., MPLS PE routers, data center switches). NNI metrics
        include throughput (bytes in/out), packet counts, error rates, and utilization for
        each NNI connection. Use this to monitor the health and capacity of gateway hand-off
        links and to identify potential congestion or errors at the network boundary.

        Returns:
            Array of NNI metric objects for each partner gateway NNI connection, containing
            throughput, packet counts, error rates, and utilization metrics over the
            specified interval
        """
        return await self.portal_list(
            "monitoring/getEnterpriseNniMetrics",
            request,
            GetEnterpriseNniMetricsResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_nni_series(
        self: VcoClientBaseProtocol, request: GetEnterpriseNniSeries
    ) -> Sequence[GetEnterpriseNniSeriesResultItem]:
        """Get partner gateway NNI metric time series data for the enterprise.

        Retrieves NNI (Network-to-Network Interface) metric data as a time series for partner
        gateways serving the specified enterprise. While getEnterpriseNniMetrics returns
        aggregate/summary metrics, this endpoint returns data points over time, enabling trend
        analysis and visualization of NNI performance. The time series includes periodic
        samples of throughput, packet counts, and utilization for each NNI connection between
        VeloCloud Partner Gateways and the partner's network infrastructure. Use this to chart
        NNI utilization trends, identify peak usage periods, detect gradual degradation, or
        correlate NNI performance with application quality issues.

        Returns:
            Array of NNI time series objects, each containing a series of timestamped data
            points with throughput, packet counts, and utilization metrics for the partner
            gateway NNI connection
        """
        return await self.portal_list(
            "monitoring/getEnterpriseNniSeries",
            request,
            GetEnterpriseNniSeriesResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_network_gateway_status(
        self: VcoClientBaseProtocol, request: GetNetworkGatewayStatus
    ) -> Sequence[GetNetworkGatewayStatusResultItem]:
        """Get point-in-time network gateway health and capacity status.

        Fetches one health-statistics sample for all VeloCloud Gateways in the network or a
        given list of target Gateways. The optional ``time`` selects a single absolute sample;
        it is not a duration or interval. Omit it for the latest available status. Gateway
        health metrics include CPU/memory utilization, connected Edge counts, tunnel counts,
        active flow counts, and handoff queue drops.

        Returns:
            Array of objects, one per Gateway, each containing the Gateway identification
            information and health metric summaries including CPU/memory utilization,
            connected Edge count, tunnel count, and throughput metrics
        """
        return await self.portal_list(
            "monitoring/getNetworkGatewayStatus",
            request,
            GetNetworkGatewayStatusResultItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_wss_location_creation_status(
        self: VcoClientBaseProtocol, request: GetWssLocationCreationStatus
    ) -> Sequence[dict[str, Any]]:
        """Get the WSS (Web Security Service) location creation status.

        Retrieves the status of location provisioning for cloud-based Web Security Services
        (WSS) integrated with VeloCloud SD-WAN. In VeloCloud, WSS integrations (such as
        Zscaler or Symantec WSS) require location objects to be created in the cloud security
        provider's platform to represent each Edge or site. This endpoint reports the
        provisioning status of those location objects, indicating whether locations have been
        successfully created, are pending, or have encountered errors during the automated
        provisioning process. Use this to monitor and troubleshoot the automated setup of
        cloud security service integrations across the enterprise.

        Returns:
            WSS location creation status records for each site/Edge, including
            provisioning state (created/pending/error) and any error details.
        """
        result = await self.portal_paginated_list(
            "monitoring/getWssLocationCreationStatus",
            request,
            RootModel[dict[str, Any]],
        )

        return [item.root for item in result]

    @domain_tag(PM_QOE)
    async def get_aggregate_edge_link_metrics(
        self: VcoClientBaseProtocol, request: GetAggregateEdgeLinkMetrics
    ) -> Sequence[AggregateEdgeLinkMetricsItem]:
        """Get aggregate Edge link metrics across all edges in the enterprise.

        Returns aggregate link/transport metrics for all active Edge WAN links (across all
        Edges available for the specified enterprise) over the specified time interval. A link
        is considered active if an Edge has reported any activity for it in the last 24 hours.
        Use this to get a high-level view of network utilization across the entire enterprise,
        including throughput, packet counts, and latency for each WAN link on every edge.

        Returns:
            Array of network utilization metric objects, one per active WAN link, including
            throughput, packet counts, and latency data for each link across all edges
        """
        return await self.portal_list(
            "monitoring/getAggregateEdgeLinkMetrics",
            request,
            AggregateEdgeLinkMetricsItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_enterprise_gateway_route_table_config(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseGatewayRouteTableConfig,
    ) -> EnterpriseGatewayRouteTableConfigResult:
        """Get the gateway and segment list for gateway route table queries.

        Returns the gateway and segment information needed to query gateway-level route tables
        for the specified enterprise. This provides the mapping between gateways and segments,
        which is required to look up routes maintained at the gateway layer. In the VeloCloud
        architecture, gateways maintain route tables for cloud-routed traffic. Useful as a
        prerequisite step for querying specific gateway route tables and understanding
        gateway-segment associations.

        Returns:
            Object containing gateway list and segment list with their associations
        """
        return await self.portal(
            "monitoring/getEnterpriseGatewayRouteTableConfig",
            request,
            EnterpriseGatewayRouteTableConfigResult,
        )

    # --- Link Insights endpoints (VCO 7.0+) ---

    @domain_tag(PM_QOE)
    async def get_link_insights_overview(
        self: VcoClientBaseProtocol, request: GetLinkInsightsOverview
    ) -> GetLinkInsightsOverviewResult:
        """Retrieve an enterprise-wide link insights overview.

        Returns total link, edge, and ISP counts along with link-flapping affected
        counts and before/after SD-WAN degradation totals for the requested
        interval.

        Returns:
            Overview with affected counts and degradation totals
        """
        return await self.portal(
            "monitoring/getLinkInsightsOverviewInfo",
            request,
            GetLinkInsightsOverviewResult,
        )

    @domain_tag(PM_QOE)
    async def get_link_flap_summary(
        self: VcoClientBaseProtocol, request: GetLinkFlapSummary
    ) -> GetLinkFlapSummaryResult:
        """Retrieve top links, edges, or ISPs ranked by emitted LINK_FLAPPING episodes.

        Returns a ranked list of entities with their emitted flap episode counts.
        The ranking dimension is controlled by the ``view_by`` parameter
        (link, edge, or isp).

        Returns:
            Top N flap summary items with an optional Others Avg row
        """
        return await self.portal(
            "monitoring/getLinkFlapSummary",
            request,
            GetLinkFlapSummaryResult,
        )

    @domain_tag(PM_QOE)
    async def get_link_degradation_summary(
        self: VcoClientBaseProtocol, request: GetLinkDegradationSummary
    ) -> GetLinkDegradationSummaryResult:
        """Retrieve top links, edges, or ISPs ranked by degradation minutes.

        Returns a ranked list of entities with their before/after SD-WAN
        degradation minutes.  The ranking dimension is controlled by the
        ``view_by`` parameter (link, edge, or isp).  The response has a
        hardcoded sort order (total degradation descending).

        Returns:
            Top N degradation summary items
        """
        return await self.portal(
            "monitoring/getLinkDegradationSummary",
            request,
            GetLinkDegradationSummaryResult,
        )

    @domain_tag(PM_QOE)
    async def get_link_degradation_details(
        self: VcoClientBaseProtocol, request: GetLinkDegradationDetails
    ) -> GetLinkDegradationDetailsResult:
        """Retrieve per-link degradation details with uptime, QoE, and flap counts.

        Returns per-link detail including uptime percentage, current state,
        before/after SD-WAN degradation, QoE metrics (loss, jitter, latency),
        and emitted flap count.  Supports filtering, sorting, and encrypted-token
        pagination via ``next_page_link`` / ``prev_page_link``. When ``count`` is
        true, the result contains only ``total_count``.

        Returns:
            Paginated degradation detail items with metadata
        """
        return await self.portal(
            "monitoring/getLinkDegradationDetails",
            request,
            GetLinkDegradationDetailsResult,
        )

    # --- linkQualityEvent/ endpoints ---

    @domain_tag(PM_QOE)
    async def get_link_quality_events(
        self: VcoClientBaseProtocol, request: GetLinkQualityEvents
    ) -> GetLinkQualityEventsResult:
        """
        Get per-link and overall Quality of Experience (QoE) scores for an Edge.

        Fetches link quality data from the VeloCloud Orchestrator that measures the quality of
        each WAN link attached to an Edge over a specified time interval. For each link, the
        response includes an aggregate totalScore, a score breakdown by traffic type, a
        timeseries of samples with pre- and post-SD-WAN-enhancement link states, and quality
        state distributions. It also provides an overallLinkQuality summary across all links.

        QoE scores are computed from sampled events stored on the Orchestrator. Scores are
        broken down by traffic type using integer keys: 0=Voice, 1=Video, 2=Transactional.
        Quality states in distribution summaries use: 0=Offline, 1=Unknown, 2=Red (poor),
        3=Yellow (fair), 4=Green (good). Timeseries samples include pre/post enhancement
        metrics for latency (Rx/Tx), jitter (Rx/Tx), and loss (Rx/Tx), as well as the
        SD-WAN optimization action applied (None, Avoid, Jitter Buffer, Error Correction,
        Forward Error Correction).

        Use this to assess WAN link health, identify degraded links, and evaluate the
        effectiveness of SD-WAN traffic optimization on each link.

        Args:
            vco_host: VCO hostname (e.g., "vco123.velocloud.net")
            api_token: User's VCO API token
            enterprise_id: Enterprise ID
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch
            edge_id: Optional edge ID to filter by a specific Edge
            max_samples: Optional maximum number of link quality events sampled to produce
                the time series (default 50). Cannot be used with minutes_per_sample.
            minutes_per_sample: Optional duration in minutes of the interval partitions
                from which events are sampled. Cannot be used with max_samples.
            individual_scores: Optional flag; when true, each sample in the time series
                is populated with its own aggregate score values and quality state
                distribution (default false)
            links: Optional list of link IDs to filter results to specific WAN links

        Returns:
            Array of link quality objects, one per link, each containing totalScore,
            per-traffic-type score breakdowns, timeseries samples with pre/post enhancement
            metrics, and quality state distributions. Also includes an overallLinkQuality
            summary aggregated across all links.
        """
        try:
            return await self.portal(
                "linkQualityEvent/getLinkQualityEvents",
                request,
                GetLinkQualityEventsResult,
            )
        except ValidationError:
            # VCO returns an empty list [] when no link quality data exists
            return GetLinkQualityEventsResult()
