from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import (
    PM_FLOW,
    PM_HEALTH,
    PM_QOE,
    SECURITY,
    domain_tag,
)
from pyvelo.client.schemas.metrics import (
    EdgeAppLinkMetricsItem,
    EdgeAppLinkSeriesItem,
    EdgeAppMetricsItem,
    EdgeAppSeriesItem,
    EdgeCategoryMetricsItem,
    EdgeCategorySeriesItem,
    EdgeDestMetricsItem,
    EdgeDestSeriesItem,
    EdgeDeviceMetricsItem,
    EdgeDeviceSeriesItem,
    EdgeLinkMetricsItem,
    EdgeLinkSeriesItem,
    EdgeNetworkInterfaceMetricsItem,
    EdgeNetworkInterfaceSeriesItem,
    EdgeSegmentMetricsItem,
    EdgeSegmentSeriesItem,
    EdgeStatusMetricsResult,
    EdgeStatusSeriesResult,
    EnterpriseFirewallEdgeCountMetricsResult,
    EnterpriseFlowMetricsItem,
    FirewallEdgeSummaryMetricsItem,
    FirewallIdpsMetricsItem,
    FirewallIdpsSeriesItem,
    FirewallMaliciousIpMetricsItem,
    FirewallUrlCategoryMetricsItem,
    FirewallUrlReputationMetricsItem,
    FlowVisibilityItem,
    GatewayStatusMetricsResult,
    GatewayStatusSeriesResult,
    GetEdgeAppLinkMetrics,
    GetEdgeAppLinkSeries,
    GetEdgeAppMetrics,
    GetEdgeAppSeries,
    GetEdgeCategoryMetrics,
    GetEdgeCategorySeries,
    GetEdgeDestMetrics,
    GetEdgeDestSeries,
    GetEdgeDeviceMetrics,
    GetEdgeDeviceSeries,
    GetEdgeFirewallIdpsMetrics,
    GetEdgeFirewallIdpsSeries,
    GetEdgeFirewallMaliciousIpMetrics,
    GetEdgeFirewallUrlCategoryMetrics,
    GetEdgeFirewallUrlReputationMetrics,
    GetEdgeFlowVisibilityMetrics,
    GetEdgeLinkMetrics,
    GetEdgeLinkSeries,
    GetEdgeNetworkInterfaceMetrics,
    GetEdgeNetworkInterfaceSeries,
    GetEdgeSdwanPeerPathMetrics,
    GetEdgeSdwanPeerPathSeries,
    GetEdgeSegmentMetrics,
    GetEdgeSegmentSeries,
    GetEdgeStatusMetrics,
    GetEdgeStatusSeries,
    GetEnterpriseFirewallEdgeCountMetrics,
    GetEnterpriseFirewallEdgeSummaryMetrics,
    GetEnterpriseFirewallIdpsMetrics,
    GetEnterpriseFirewallMaliciousIpMetrics,
    GetEnterpriseFirewallUrlCategoryMetrics,
    GetEnterpriseFirewallUrlReputationMetrics,
    GetEnterpriseFlowMetrics,
    GetGatewayStatusMetrics,
    GetGatewayStatusSeries,
    GetNsdEdgeTunnelStats,
    GetNsdTunnelStats,
    NsdEdgeTunnelStatsResultItem,
    NsdTunnelStatsResultItem,
    SdwanEdgePeerPathMetricsItem,
    SdwanEdgePeerPathSeriesItem,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class MetricsMixin(Protocol):
    """Operations for metrics/ API endpoints."""

    @domain_tag(PM_QOE)
    async def get_edge_sdwan_peer_path_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeSdwanPeerPathMetrics
    ) -> Sequence[SdwanEdgePeerPathMetricsItem]:
        """Retrieve aggregated SD-WAN peer path quality metrics for an edge.

        Returns aggregate statistics (min, max, average, count) for path quality
        metrics between an Edge and its SD-WAN peers (branch-to-branch or branch-to-hub)
        over a time interval. Metrics include latency, jitter, packet loss, and
        throughput per peer path. In VeloCloud DMPO (Dynamic Multi-Path Optimization),
        these paths are continuously measured and traffic is steered based on quality.
        Useful for identifying degraded peer paths and understanding overlay path
        performance between sites.

        Returns:
            Array of peer path metric summaries grouped by peer and path
        """
        return await self.portal_list(
            "metrics/getEdgeSDWANPeerPathMetrics",
            request,
            SdwanEdgePeerPathMetricsItem,
        )

    @domain_tag(PM_QOE)
    async def get_edge_sdwan_peer_path_series(
        self: VcoClientBaseProtocol, request: GetEdgeSdwanPeerPathSeries
    ) -> Sequence[SdwanEdgePeerPathSeriesItem]:
        """Retrieve time-series SD-WAN peer path quality metrics for an edge.

        Returns time-bucketed quality measurements for SD-WAN peer paths over a
        specified interval. Each series entry contains arrays of metric values at
        regular tick intervals, enabling trend analysis and visualization of path
        quality changes over time. Complements the aggregate metrics endpoint by
        providing temporal resolution for latency, jitter, and loss per peer path.

        Returns:
            Array of time-series entries per peer path, each containing metric
            data arrays with timestamps and tick intervals
        """
        return await self.portal_list(
            "metrics/getEdgeSDWANPeerPathSeries",
            request,
            SdwanEdgePeerPathSeriesItem,
        )

    @domain_tag(PM_FLOW)
    async def get_enterprise_flow_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFlowMetrics
    ) -> Sequence[EnterpriseFlowMetricsItem]:
        """Retrieve flow-level traffic metrics for an enterprise over a time interval.

        Returns per-flow traffic statistics aggregated across the enterprise's Edge
        fleet. Flow metrics capture application-level traffic details including source
        and destination, bytes/packets transferred, and the business policy action
        applied. In VeloCloud SD-WAN, flows are classified by the DPI engine and
        matched against business policy rules for steering, QoS, and firewall
        decisions. Useful for traffic analysis, capacity planning, and validating
        that business policies are classifying and steering traffic as expected.

        Returns:
            Array of flow metric entries with traffic statistics and classification info
        """
        return await self.portal_list(
            "metrics/getEnterpriseFlowMetrics",
            request,
            EnterpriseFlowMetricsItem,
        )

    @domain_tag(PM_FLOW)
    async def get_edge_app_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeAppMetrics
    ) -> Sequence[EdgeAppMetricsItem]:
        """Get flow metric aggregate data by application for an edge.

        Returns flow metric summaries (bytes, packets, duration) for the specified edge
        over the given time interval, grouped by application (e.g., HTTP, Office365, Zoom).
        Each entry in the result corresponds to a single application.

        Returns:
            Array of flow data objects, one per application, with traffic metrics
        """
        return await self.portal_list(
            "metrics/getEdgeAppMetrics", request, EdgeAppMetricsItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_device_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeDeviceMetrics
    ) -> Sequence[EdgeDeviceMetricsItem]:
        """Get flow metric aggregate data by client device for an edge.

        Returns flow metric summaries for the specified edge and time interval,
        grouped by client device. Each entry corresponds to a distinct client device
        (identified by IP/MAC) that generated traffic for the specified application.

        Returns:
            Array of flow data objects, one per client device
        """
        return await self.portal_list(
            "metrics/getEdgeDeviceMetrics", request, EdgeDeviceMetricsItem
        )

    @domain_tag(PM_HEALTH)
    async def get_edge_status_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeStatusMetrics
    ) -> EdgeStatusMetricsResult:
        """Get edge healthStats metric summaries for an interval.

        Returns min, max, and average values of edge health metrics over the given
        time interval. Metrics include tunnel count, CPU usage, memory usage,
        flow count, handoff queue drops, route count, NAT count, and utilization percentages.

        Returns:
            Object with min/max/avg values for each requested metric (e.g., tunnelCount, cpuPct, memoryPct, flowCount)
        """
        return await self.portal(
            "metrics/getEdgeStatusMetrics", request, EdgeStatusMetricsResult
        )

    @domain_tag(PM_QOE)
    async def get_edge_link_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeLinkMetrics
    ) -> Sequence[EdgeLinkMetricsItem]:
        """Get summary transport metrics by WAN link for an edge.

        Returns aggregate transport metrics (bytesRx, bytesTx, latencyMs, jitterMs,
        lostPct) for each WAN link on the specified edge over the given time interval.
        Each entry corresponds to a WAN link that reported activity during the period.

        Returns:
            Array of link metrics objects, one per WAN link
        """
        return await self.portal_list(
            "metrics/getEdgeLinkMetrics", request, EdgeLinkMetricsItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_flow_visibility_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeFlowVisibilityMetrics
    ) -> Sequence[FlowVisibilityItem]:
        """Get individual flow stats for an edge.

        Returns detailed per-flow statistics showing individual network flows (connections)
        traversing the edge. Each entry corresponds to a single flow with source/destination,
        application, bytes transferred, and routing information. Useful for deep traffic
        inspection and troubleshooting specific connections.

        Returns:
            Flow rows, without VCO's per-page "other" summary row
        """
        items = await self.portal_paginated_list(
            "metrics/getEdgeFlowVisibilityMetrics",
            request,
            FlowVisibilityItem,
        )
        return [item for item in items if not item.is_summary_row()]

    @domain_tag(PM_QOE)
    async def get_edge_link_series(
        self: VcoClientBaseProtocol, request: GetEdgeLinkSeries
    ) -> Sequence[EdgeLinkSeriesItem]:
        """Get WAN link transport metric time series for an edge.

        Returns timestamped transport metrics (throughput, latency, jitter, packet loss)
        for each WAN link on the specified edge over the given interval. Each entry
        corresponds to a link, containing an array of time-series data points.

        Returns:
            Array of link time-series objects, one per WAN link
        """
        return await self.portal_list(
            "metrics/getEdgeLinkSeries", request, EdgeLinkSeriesItem
        )

    @domain_tag(PM_HEALTH)
    async def get_edge_status_series(
        self: VcoClientBaseProtocol, request: GetEdgeStatusSeries
    ) -> EdgeStatusSeriesResult:
        """Get edge healthStats time series for an interval.

        Returns timestamped health statistics (CPU, memory, tunnel count, flow count,
        handoff queue drops, route count) as time series data. Similar to
        get_edge_status_metrics but returns individual data points over time instead
        of min/max/avg summaries.

        Returns:
            Array of healthStats time series data points
        """
        return await self.portal(
            "metrics/getEdgeStatusSeries", request, EdgeStatusSeriesResult
        )

    @domain_tag(PM_FLOW)
    async def get_edge_app_series(
        self: VcoClientBaseProtocol, request: GetEdgeAppSeries
    ) -> Sequence[EdgeAppSeriesItem]:
        """Get flow metric time series data by application for an edge.

        Returns timestamped flow metrics (bytes, packets) grouped by application
        (e.g., HTTP, Office365, Zoom) over the given time interval. Each entry
        corresponds to a single application with its time series data.

        Returns:
            Array of per-application time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeAppSeries", request, EdgeAppSeriesItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_category_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeCategoryMetrics
    ) -> Sequence[EdgeCategoryMetricsItem]:
        """Get flow metric aggregate data by application category for an edge.

        Returns flow metric summaries grouped by application category (e.g., "Voice & Video",
        "Cloud Services", "Social Networking", "Web"). Categories are logical groupings of
        applications defined in the VCO. Each entry corresponds to a category of application
        traffic that has traversed the edge.

        Returns:
            Array of flow data objects, one per application category
        """
        return await self.portal_list(
            "metrics/getEdgeCategoryMetrics", request, EdgeCategoryMetricsItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_dest_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeDestMetrics
    ) -> Sequence[EdgeDestMetricsItem]:
        """Get flow metric aggregate data by destination for an edge.

        Returns flow metric summaries grouped by traffic destination (domain/IP).
        Each entry corresponds to a distinct destination that the edge sent traffic to.
        Useful for identifying top bandwidth consumers and traffic patterns by destination.

        Returns:
            Array of flow data objects, one per traffic destination
        """
        return await self.portal_list(
            "metrics/getEdgeDestMetrics", request, EdgeDestMetricsItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_segment_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeSegmentMetrics
    ) -> Sequence[EdgeSegmentMetricsItem]:
        """Get flow metric aggregate data by network segment for an edge.

        Returns flow metric summaries grouped by segment ID. Segments are isolated
        virtual network partitions within SD-WAN (similar to VRFs/VLANs) used to
        separate traffic for different business units or security zones. Each entry
        corresponds to a segment that carried traffic on the edge.

        Returns:
            Array of flow data objects, one per network segment
        """
        return await self.portal_list(
            "metrics/getEdgeSegmentMetrics", request, EdgeSegmentMetricsItem
        )

    @domain_tag(PM_HEALTH)
    async def get_edge_network_interface_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeNetworkInterfaceMetrics
    ) -> Sequence[EdgeNetworkInterfaceMetricsItem]:
        """Get network interface stats summaries for an edge.

        Returns aggregate metrics for the edge's physical and logical network interfaces
        (e.g., GE1, GE2, SFP1) over the given interval. Includes per-interface counters
        such as bytes/packets in/out, errors, drops, and link speed.

        Returns:
            Array of network interface metric objects
        """
        return await self.portal_list(
            "metrics/getEdgeNetworkInterfaceMetrics",
            request,
            EdgeNetworkInterfaceMetricsItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_edge_network_interface_series(
        self: VcoClientBaseProtocol, request: GetEdgeNetworkInterfaceSeries
    ) -> Sequence[EdgeNetworkInterfaceSeriesItem]:
        """Get network interface stats time series for an edge.

        Returns timestamped network interface statistics for each physical/logical
        interface on the edge. Each item corresponds to a distinct interface (e.g., GE1,
        GE2) with time series data for bytes/packets in/out, errors, and drops.

        Returns:
            Array of per-interface time series objects
        """
        return await self.portal_list(
            "metrics/getEdgeNetworkInterfaceSeries",
            request,
            EdgeNetworkInterfaceSeriesItem,
        )

    @domain_tag(PM_FLOW)
    async def get_edge_app_link_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeAppLinkMetrics
    ) -> Sequence[EdgeAppLinkMetricsItem]:
        """Get flow metric aggregate data by WAN link for an edge.

        Returns flow metric summaries (bytes, packets, duration) grouped by WAN link.
        Each entry corresponds to a link on the edge, showing how application traffic
        was distributed across WAN links. Useful for understanding link utilization
        at the application flow level.

        Returns:
            Array of flow data objects, one per WAN link
        """
        return await self.portal_list(
            "metrics/getEdgeAppLinkMetrics", request, EdgeAppLinkMetricsItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_app_link_series(
        self: VcoClientBaseProtocol, request: GetEdgeAppLinkSeries
    ) -> Sequence[EdgeAppLinkSeriesItem]:
        """Get flow metric time series data by WAN link for an edge.

        Returns timestamped flow metrics grouped by WAN link over the given interval.
        Each entry corresponds to a link on the edge with its time series data, showing
        how application traffic distribution across links changed over time.

        Returns:
            Array of per-link time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeAppLinkSeries", request, EdgeAppLinkSeriesItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_category_series(
        self: VcoClientBaseProtocol, request: GetEdgeCategorySeries
    ) -> Sequence[EdgeCategorySeriesItem]:
        """Get flow metric time series data by application category for an edge.

        Returns timestamped flow metrics grouped by application category (e.g., "Voice & Video",
        "Cloud Services", "Social Networking", "Web") over the given interval. Each entry
        corresponds to a category of application traffic with its time series data.

        Returns:
            Array of per-category time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeCategorySeries", request, EdgeCategorySeriesItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_dest_series(
        self: VcoClientBaseProtocol, request: GetEdgeDestSeries
    ) -> Sequence[EdgeDestSeriesItem]:
        """Get flow metric time series data by destination for an edge.

        Returns timestamped flow metrics grouped by traffic destination (domain/IP) over
        the given interval. Each entry corresponds to a distinct destination with its
        time series data.

        Returns:
            Array of per-destination time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeDestSeries", request, EdgeDestSeriesItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_device_series(
        self: VcoClientBaseProtocol, request: GetEdgeDeviceSeries
    ) -> Sequence[EdgeDeviceSeriesItem]:
        """Get flow metric time series data by client device for an edge.

        Returns timestamped flow metrics grouped by client device (identified by IP/MAC)
        over the given interval. Each entry corresponds to a distinct client device
        with its time series data.

        Returns:
            Array of per-device time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeDeviceSeries", request, EdgeDeviceSeriesItem
        )

    @domain_tag(PM_FLOW)
    async def get_edge_segment_series(
        self: VcoClientBaseProtocol, request: GetEdgeSegmentSeries
    ) -> Sequence[EdgeSegmentSeriesItem]:
        """Get flow metric time series data by network segment for an edge.

        Returns timestamped flow metrics grouped by segment ID over the given interval.
        Segments are isolated virtual network partitions within SD-WAN (similar to VRFs/VLANs).
        Each entry corresponds to a segment with its time series data.

        Returns:
            Array of per-segment time series flow data
        """
        return await self.portal_list(
            "metrics/getEdgeSegmentSeries", request, EdgeSegmentSeriesItem
        )

    @domain_tag(PM_HEALTH)
    async def get_gateway_status_metrics(
        self: VcoClientBaseProtocol, request: GetGatewayStatusMetrics
    ) -> GatewayStatusMetricsResult:
        """Get Gateway health metric summaries (min, max, average) for a time interval.

        Fetches health metric summaries for a target gateway over the specified time interval.
        Gateways in VeloCloud SD-WAN serve as regional hub nodes that handle VCMP tunnel
        termination, multi-path optimization, and traffic routing between edges and the
        network backbone. Monitoring gateway health is critical for ensuring optimal SD-WAN
        performance. Available metrics include: tunnelCount (active VCMP tunnels),
        tunnelCountV6 (IPv6 tunnels), cpuPct (CPU utilization percentage), memoryPct
        (memory utilization percentage), flowCount (active flow count), handoffQueueDrops
        (packets dropped in handoff queues), and connectedEdges (number of edges connected
        to this gateway).

        Returns:
            Object containing min, max, and average values for each requested metric
            (tunnelCount, memoryPct, flowCount, cpuPct, handoffQueueDrops,
            connectedEdges, tunnelCountV6)
        """
        return await self.portal(
            "metrics/getGatewayStatusMetrics", request, GatewayStatusMetricsResult
        )

    @domain_tag(PM_HEALTH)
    async def get_gateway_status_series(
        self: VcoClientBaseProtocol, request: GetGatewayStatusSeries
    ) -> GatewayStatusSeriesResult:
        """Get Gateway health metric time series data for a time interval.

        Fetches health metric time series for a target gateway over the specified time
        interval. Unlike the summary endpoint (get_gateway_status_metrics) which returns
        min/max/average aggregates, this returns data points over time, enabling trend
        analysis and visualization of gateway health. Each metric produces its own time
        series with regular tick intervals. Use this to chart gateway CPU, memory, tunnel
        count, flow count, or handoff queue drops over time to identify performance
        degradation, capacity issues, or correlate gateway health with network events.

        Returns:
            Object with a "series" array containing one time series per metric, each
            with metric name, start time, tick interval, and an array of data points
        """
        return await self.portal(
            "metrics/getGatewayStatusSeries", request, GatewayStatusSeriesResult
        )

    @domain_tag(SECURITY)
    async def get_edge_firewall_idps_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeFirewallIdpsMetrics
    ) -> Sequence[FirewallIdpsMetricsItem]:
        """Get aggregate Firewall IDPS (Intrusion Detection and Prevention System) metrics for a specific edge.

        Returns Firewall IDPS summaries for the specified enterprise and edge over the given
        time interval, grouped by dimension (e.g., signatureName, threatImpact, alert,
        threatSourceIp, threatSourceGeoCountry, threatTargetIp). Each entry corresponds to a
        single dimension value. The Enhanced Firewall Service (EFS) IDPS engine inspects
        traffic for known threat signatures and reports detections. Use this to investigate
        security threats detected on a specific edge, including threat counts, severity
        levels, signature categories, and source/target IP addresses.

        Returns:
            Array of aggregate IDPS data objects, each containing threat counts, signature
            IDs, threat impact levels (HIGH/MEDIUM/LOW), and signature categories
            (e.g., "command-and-control", "trojan-activity")
        """
        return await self.portal_list(
            "metrics/getEdgeFirewallIdpsMetrics", request, FirewallIdpsMetricsItem
        )

    @domain_tag(SECURITY)
    async def get_edge_firewall_idps_series(
        self: VcoClientBaseProtocol, request: GetEdgeFirewallIdpsSeries
    ) -> Sequence[FirewallIdpsSeriesItem]:
        """Get Firewall IDPS time series data for a specific edge by dimension.

        Returns Firewall IDPS time series for the specified enterprise or edge over the given
        time interval, grouped by dimension (e.g., threatImpact). Each entry corresponds to a
        single dimension value and contains a time series of threat counts at regular tick
        intervals. Use this to visualize IDPS threat activity trends over time, broken down
        by severity level (HIGH, MEDIUM, LOW), to identify spikes or patterns in intrusion
        detection events on a specific edge.

        Returns:
            Array of time series objects, each with a dimension name (e.g., "HIGH", "MEDIUM",
            "LOW"), containing series data with metric name, start time, tick interval, data
            points array, total, min, and max values
        """
        return await self.portal_list(
            "metrics/getEdgeFirewallIdpsSeries", request, FirewallIdpsSeriesItem
        )

    @domain_tag(SECURITY)
    async def get_edge_firewall_malicious_ip_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeFirewallMaliciousIpMetrics
    ) -> Sequence[FirewallMaliciousIpMetricsItem]:
        """Get aggregate Firewall Malicious IP metrics for a specific edge.

        Returns Firewall Malicious IP summaries for the specified enterprise and edge over the
        given time interval, grouped by dimension (e.g., destIp, action, ipCategory, sourceIp,
        threatSourceGeoCountry). Each entry corresponds to a single dimension value. The
        Enhanced Firewall Service (EFS) malicious IP engine checks traffic against known
        malicious IP address databases and can monitor or block connections. Use this to
        investigate malicious IP activity on a specific edge, including which IPs were
        flagged, what actions were taken (block/monitor), and the geographic origin of threats.

        Returns:
            Array of aggregate malicious IP data objects with threat counts
            (threatsCount, monitoredThreatsCount, blockedThreatsCount) for each
            dimension value (e.g., IP addresses, actions, IP categories, countries)
        """
        return await self.portal_list(
            "metrics/getEdgeFirewallMaliciousIpMetrics",
            request,
            FirewallMaliciousIpMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_edge_firewall_url_category_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeFirewallUrlCategoryMetrics
    ) -> Sequence[FirewallUrlCategoryMetricsItem]:
        """Get aggregate URL category filtering metrics for a specific edge.

        Returns URL category summaries for the specified enterprise and edge over the given
        time interval, grouped by dimension (e.g., urlCategory, action, sourceIp). Each entry
        corresponds to a single dimension value. The Enhanced Firewall Service (EFS) URL
        filtering engine categorizes web traffic into content categories (e.g., "Gambling",
        "News", "Social Networking") and can allow, monitor, or block access. Use this to
        understand web browsing patterns by category on a specific edge and to see which
        categories are being blocked or monitored by URL filtering policies.

        Returns:
            Array of aggregate URL category data objects with counts per category
            (threatsCount, urlsBlockedCount, urlsMonitoredCount, urlsAllowedCount)
        """
        return await self.portal_list(
            "metrics/getEdgeFirewallUrlCategoryMetrics",
            request,
            FirewallUrlCategoryMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_edge_firewall_url_reputation_metrics(
        self: VcoClientBaseProtocol, request: GetEdgeFirewallUrlReputationMetrics
    ) -> Sequence[FirewallUrlReputationMetricsItem]:
        """Get aggregate URL reputation metrics for a specific edge.

        Returns URL reputation summaries for the specified edge over the given time interval,
        grouped by dimension (e.g., domainName, sourceIp, urlRisk, action). Each entry
        corresponds to a single dimension value. The Enhanced Firewall Service (EFS) URL
        reputation engine scores URLs by risk level (high, medium, low, trustworthy,
        suspicious) and can allow, monitor, or block access based on reputation. Use this
        to identify risky domains accessed from a specific edge, see which source IPs are
        visiting high-risk URLs, and review actions taken by the URL reputation policy.

        Returns:
            Array of aggregate URL reputation data objects with counts per dimension
            (urlsHighRiskCount, urlsMediumRiskCount, urlsLowRiskCount,
            urlsTrustworthyCount, urlsSuspiciousCount, urlsBlockedCount,
            urlsMonitoredCount, urlsAllowedCount)
        """
        return await self.portal_list(
            "metrics/getEdgeFirewallUrlReputationMetrics",
            request,
            FirewallUrlReputationMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_idps_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallIdpsMetrics
    ) -> Sequence[FirewallIdpsMetricsItem]:
        """Get aggregate Firewall IDPS metrics across all edges in the enterprise.

        Returns Firewall IDPS summaries for the entire enterprise over the specified time
        interval, grouped by dimension (e.g., signatureName, threatImpact, alert,
        threatSourceIp, threatSourceGeoCountry, threatTargetIp, edgeLogicalId). Each entry
        corresponds to a single dimension value. Unlike the edge-level IDPS metrics, this
        provides a consolidated view of intrusion detection events across all edges in the
        enterprise. Use this to get an enterprise-wide security posture, identify the most
        common threat signatures, or find which edges are seeing the most IDPS activity.

        Returns:
            Array of aggregate IDPS data objects, each containing threat counts, signature
            IDs, threat impact levels (HIGH/MEDIUM/LOW), and signature categories
            (e.g., "command-and-control", "trojan-activity")
        """
        return await self.portal_list(
            "metrics/getEnterpriseFirewallIdpsMetrics",
            request,
            FirewallIdpsMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_edge_count_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallEdgeCountMetrics
    ) -> EnterpriseFirewallEdgeCountMetricsResult:
        """Get total and impacted Edge counts for an enterprise based on Enhanced Firewall Service (EFS) records.

        Returns the total number of edges in the enterprise as well as the number of edges
        that are protected by the Enhanced Firewall Service (EFS). This provides a quick
        overview of EFS deployment coverage across the enterprise. Use this to determine
        how many edges have EFS enabled and how many have been impacted by security events
        (IDPS detections, malicious IP blocks, URL filtering hits) during the specified
        time interval.

        Returns:
            Object with totalEdgesCount (total edges in enterprise) and
            impactedEdgesCount (edges with EFS security events)
        """
        return await self.portal(
            "metrics/getEnterpriseFirewallEdgeCountMetrics",
            request,
            EnterpriseFirewallEdgeCountMetricsResult,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_edge_summary_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallEdgeSummaryMetrics
    ) -> Sequence[FirewallEdgeSummaryMetricsItem]:
        """Get aggregate Firewall Edge Summary based on actions taken for various security engine types.

        Returns a per-edge summary of Enhanced Firewall Service (EFS) activity across the
        enterprise for the specified time interval, broken down by security engine type:
        IDPS (Intrusion Detection/Prevention), URL Filtering (category-based web filtering),
        URL Reputation (risk-based URL scoring), and Malicious IP (known bad IP blocking).
        Each entry corresponds to a single edge and shows the actions taken (allow, monitor,
        block) for each engine type. Use this to compare firewall activity across edges and
        identify which edges have the most security events.

        Returns:
            Array of per-edge summary objects, each containing action counts for
            idpsActions, urlCatActions, urlRepActions, and malIpActions engine types
        """
        return await self.portal_list(
            "metrics/getEnterpriseFirewallEdgeSummaryMetrics",
            request,
            FirewallEdgeSummaryMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_malicious_ip_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallMaliciousIpMetrics
    ) -> Sequence[FirewallMaliciousIpMetricsItem]:
        """Get aggregate Firewall Malicious IP metrics across all edges in the enterprise.

        Returns Firewall Malicious IP summaries for the entire enterprise over the specified
        time interval, grouped by dimension (e.g., destIp, action, ipCategory, sourceIp,
        threatSourceGeoCountry). Each entry corresponds to a single dimension value. Unlike
        the edge-level malicious IP metrics, this provides a consolidated view across all
        edges. Use this to get an enterprise-wide view of malicious IP activity, including
        which destination IPs are flagged, which source IPs are communicating with malicious
        hosts, and the geographic distribution of threat sources.

        Returns:
            Array of aggregate malicious IP data objects with threat counts
            (threatsCount, monitoredThreatsCount, blockedThreatsCount) for each
            dimension value
        """
        return await self.portal_list(
            "metrics/getEnterpriseFirewallMaliciousIpMetrics",
            request,
            FirewallMaliciousIpMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_url_category_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallUrlCategoryMetrics
    ) -> Sequence[FirewallUrlCategoryMetricsItem]:
        """Get aggregate URL category filtering metrics across all edges in the enterprise.

        Returns URL category summaries for the entire enterprise over the specified time
        interval, grouped by dimension (e.g., urlCategory, action, sourceIp, edgeLogicalId).
        Each entry corresponds to a single dimension value. Unlike the edge-level URL category
        metrics, this provides a consolidated view across all edges. Use this to understand
        enterprise-wide web browsing patterns by content category, identify which URL
        categories are most frequently blocked or monitored, and see which edges or source
        IPs generate the most URL filtering events.

        Returns:
            Array of aggregate URL category data objects with counts per dimension
            (threatsCount, urlsBlockedCount, urlsAllowedCount, urlsMonitoredCount)
        """
        return await self.portal_list(
            "metrics/getEnterpriseFirewallUrlCategoryMetrics",
            request,
            FirewallUrlCategoryMetricsItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_url_reputation_metrics(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallUrlReputationMetrics
    ) -> Sequence[FirewallUrlReputationMetricsItem]:
        """Get aggregate URL reputation metrics across all edges in the enterprise.

        Returns URL reputation summaries for the entire enterprise over the specified time
        interval, grouped by dimension (e.g., domainName, edgeLogicalId). Each entry
        corresponds to a single dimension value. Unlike the edge-level URL reputation metrics,
        this provides a consolidated view across all edges. Use this to identify the riskiest
        domains accessed enterprise-wide, see which edges are visiting high-risk URLs, and
        review the overall URL reputation posture across the organization.

        Returns:
            Array of aggregate URL reputation data objects with risk-level counts
            (urlsHighRiskCount, urlsMediumRiskCount, urlsLowRiskCount,
            urlsTrustworthyCount, urlsSuspiciousCount) for each dimension value
        """
        return await self.portal_list(
            "metrics/getEnterpriseFirewallUrlReputationMetrics",
            request,
            FirewallUrlReputationMetricsItem,
        )

    @domain_tag(PM_HEALTH)
    async def get_nsd_tunnel_stats(
        self: VcoClientBaseProtocol, request: GetNsdTunnelStats
    ) -> Sequence[NsdTunnelStatsResultItem]:
        """Get NSD (Non SD-WAN Destination) Gateway tunnel stats metric time series for a time interval.

        Fetches stats metric time series for a target Non SD-WAN Destination (NSD) via
        Gateway over the specified time interval. NSDs are external endpoints (e.g., cloud
        security services, third-party data centers) that are connected to the VeloCloud
        SD-WAN fabric via IPsec tunnels through gateways. Gateway-based NSD tunnels route
        traffic from edges through VeloCloud gateways before reaching the NSD. Available
        metrics include packetsRx, packetsTx, totalPackets, bytesRx, bytesTx, totalBytes,
        and flowCount. Use this to monitor throughput and traffic volume on gateway-to-NSD
        IPsec tunnels to ensure connectivity and performance to external services.

        Returns:
            Time series object with NSD tunnel stats including data center public IP and
            series data with metric name, start time, tick interval, and data points
            for each requested metric (packets, bytes, flow counts)
        """
        return await self.portal_list(
            "metrics/getNSDTunnelStats", request, NsdTunnelStatsResultItem
        )

    @domain_tag(PM_HEALTH)
    async def get_nsd_edge_tunnel_stats(
        self: VcoClientBaseProtocol, request: GetNsdEdgeTunnelStats
    ) -> Sequence[NsdEdgeTunnelStatsResultItem]:
        """Get NSD (Non SD-WAN Destination) Edge tunnel stats metric time series for a time interval.

        Fetches stats metric time series for a target Non SD-WAN Destination (NSD) via Edge
        over the specified time interval. Edge-based NSD tunnels connect directly from an
        edge to an external endpoint (e.g., cloud security services like Zscaler, Palo Alto
        Prisma) via IPsec, bypassing the gateway. This is commonly used for direct cloud
        security insertion or branch-to-cloud connectivity. Available metrics include
        packetsRx, packetsTx, totalPackets, bytesRx, bytesTx, and totalBytes. Use this to
        monitor throughput and traffic volume on edge-to-NSD IPsec tunnels to verify
        connectivity and performance to external services from a specific edge.

        Returns:
            Time series object with NSD Edge tunnel stats including NVS public IP and
            series data with metric name, start time, tick interval, and data points
            for each requested metric (packets, bytes)
        """
        return await self.portal_list(
            "metrics/getNSDEdgeTunnelStats", request, NsdEdgeTunnelStatsResultItem
        )
