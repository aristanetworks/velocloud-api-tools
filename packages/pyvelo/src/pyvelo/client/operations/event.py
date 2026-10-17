from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import EDGE, EVENTS, PM_QOE, domain_tag
from pyvelo.client.schemas.event import (
    EnterpriseAlertItem,
    GetAlertsByType,
    GetEnterpriseAlerts,
    GetEnterpriseEvents,
    GetEnterpriseEventsItem,
    GetOperatorEvents,
    GetProxyEvents,
    OperatorEventItem,
    ProxyEventItem,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class EventMixin(Protocol):
    """Operations for event/ API endpoints."""

    @domain_tag(EVENTS, PM_QOE, EDGE)
    async def get_enterprise_events(
        self: VcoClientBaseProtocol,
        request: GetEnterpriseEvents,
    ) -> Sequence[GetEnterpriseEventsItem]:
        """Retrieve enterprise events such as alerts, edge state changes, and link events."""
        return await self.portal_paginated_list(
            "event/getEnterpriseEventsList",
            request,
            GetEnterpriseEventsItem,
        )

    @domain_tag(EVENTS)
    async def get_alerts_by_type(
        self: VcoClientBaseProtocol,
        request: GetAlertsByType,
    ) -> Sequence[EnterpriseAlertItem]:
        """Get enterprise alerts filtered by alert type.

        Convenience wrapper around the enterprise alerts endpoint that pre-builds
        a filter for a specific alert type (e.g. EDGE_DOWN, LINK_DOWN, HA_FAILOVER).
        Alerts are triggered by VCO event processing rules and represent actionable
        notifications distinct from the raw event log. Useful for building alert
        dashboards or programmatically checking for active alerts of a particular type.

        Returns:
            Array of alert entries matching the specified alert type
        """
        alert_request = GetEnterpriseAlerts(
            enterprise_id=request.enterprise_id,
            interval=request.interval,
            limit=request.limit,
            filter={
                "rules": [{"field": "type", "op": "is", "values": [request.alert_type]}]
            },
        )
        return await self.portal_list(
            "enterprise/getEnterpriseAlerts",
            alert_request,
            EnterpriseAlertItem,
        )

    @domain_tag(EVENTS)
    async def get_operator_events(
        self: VcoClientBaseProtocol,
        request: GetOperatorEvents,
    ) -> AsyncGenerator[list[OperatorEventItem]]:
        """Get operator-level events for a VeloCloud network.

        Retrieves events scoped to the operator (network) context in the VeloCloud
        Orchestrator (VCO). Operator events include infrastructure-level occurrences such
        as gateway state changes, network configuration modifications, operator user login
        activity, and system-level alerts. These events are distinct from enterprise-level
        events and are typically used by network operators or service providers managing the
        SD-WAN infrastructure. Optionally filter by event type or specify a gateway ID to
        narrow results. If no end date is specified in the interval, the current date is used.

        Returns:
            Object containing a "data" array of operator event objects (each with fields like
            event type, severity, message, timestamp) and a "metaData" object with pagination
            information (total record count, etc.)
        """
        return self.portal_paginated(
            "event/getOperatorEvents",
            request,
            OperatorEventItem,
        )

    @domain_tag(EVENTS)
    async def get_proxy_events(
        self: VcoClientBaseProtocol,
        request: GetProxyEvents,
    ) -> AsyncGenerator[list[ProxyEventItem]]:
        """Fetch partner (MSP/proxy) events from the VeloCloud Orchestrator.

        Retrieves events scoped to a partner or Managed Service Provider (MSP) context. In the
        VeloCloud SD-WAN architecture, partners/MSPs manage multiple customer enterprises. Partner
        events include actions such as enterprise provisioning, partner user login activity,
        configuration changes made at the partner level, and delegated enterprise management
        operations. Optionally filter by event type or by a specific enterprise ID to narrow
        results to events affecting a particular customer. If no end date is given in the
        interval, it defaults to the current date.

        Returns:
            Object containing a "data" array of partner event objects (each with fields like
            event type, severity, message, timestamp, and associated enterprise info) and a
            "metaData" object with pagination information
        """
        return self.portal_paginated(
            "event/getProxyEvents",
            request,
            ProxyEventItem,
        )
