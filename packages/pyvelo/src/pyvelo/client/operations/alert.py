from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import EVENTS, domain_tag
from pyvelo.client.schemas.alert_definition import (
    AlertableEventItem,
    AlertableEventsResult,
    AlertDefinitionResult,
    GetEnterpriseAlertableEvents,
    GetEnterpriseAlertDefinitions,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class AlertMixin(Protocol):
    """Operations for alert/ API endpoints."""

    @domain_tag(EVENTS)
    async def get_enterprise_alert_definitions(
        self: VcoClientBaseProtocol, request: GetEnterpriseAlertDefinitions
    ) -> Sequence[AlertDefinitionResult]:
        """Get all supported alert definitions and their default settings for an enterprise.

        Lists all alert types supported for the target enterprise along with their respective
        default configuration settings. Alert definitions describe the available alert types
        (e.g., EDGE_DOWN, LINK_DOWN, HA_FAILOVER, CERTIFICATE_EXPIRY) and their default
        parameters such as severity, notification behavior, and trigger conditions. These
        definitions serve as templates from which enterprise alert configurations are created.
        Useful for understanding what alerts can be configured and their default behavior.

        Returns:
            Array of alert definition objects with alert type, default severity, default
            notification settings, and configurable parameters
        """
        return await self.portal_list(
            "alert/getEnterpriseAlertDefinitions",
            request,
            AlertDefinitionResult,
        )

    @domain_tag(EVENTS)
    async def get_enterprise_alertable_events(
        self: VcoClientBaseProtocol, request: GetEnterpriseAlertableEvents
    ) -> Sequence[AlertableEventItem]:
        """Get all enterprise event types that can be configured for alerting.

        Returns the list of enterprise event types that support alert notification configuration.
        When these event types are configured for alerting, the system will send alert
        notifications (email, webhook, etc.) whenever such events occur. Not all events support
        alerting -- this method returns only those that do. Useful for understanding which events
        can trigger alert notifications and for building alert configuration interfaces.

        Returns:
            Array of alertable event type objects with eventName and severity
        """
        result = await self.portal(
            "alert/getEnterpriseAlertableEvents",
            request,
            AlertableEventsResult,
        )
        return result.event_list
