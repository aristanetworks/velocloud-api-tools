from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pyvelo.client.operations._tagging import SECURITY, domain_tag
from pyvelo.client.schemas.firewall import (
    GetEnterpriseFirewallLogs,
    GetEnterpriseFirewallLogsList,
    GetEnterpriseFirewallLogsListItem,
    GetEnterpriseFirewallLogsResult,
    GetEnterpriseFirewallRules,
    GetEnterpriseFirewallRulesResult,
    GetMaliciousIpCategories,
    GetMaliciousIpCategoriesResult,
    GetUrlFilteringCategories,
    GetUrlFilteringCategoriesResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class FirewallMixin(Protocol):
    """Operations for firewall/ API endpoints."""

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_logs(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallLogs
    ) -> GetEnterpriseFirewallLogsResult:
        """Get firewall logs for an enterprise from the VeloCloud Orchestrator.

        Retrieves the stateful firewall log entries recorded by SD-WAN Edges in the specified
        enterprise. Firewall logs capture traffic flow decisions made by the VeloCloud Enhanced
        Firewall, including allowed, denied, and dropped connections. Each log entry typically
        includes source/destination IP and port, protocol, the firewall rule that matched, the
        action taken (allow/deny/drop), byte and packet counts, and timestamps. These logs are
        essential for security auditing, troubleshooting connectivity issues caused by firewall
        policies, and verifying that firewall rules are working as intended. Optionally filter
        by a specific Edge ID to see logs from a single site.

        Returns:
            Object containing a "data" array of firewall log entries (each with source/dest
            IP, port, protocol, action, matching rule info, byte/packet counts, timestamp),
            a "metaData" object with pagination info, and a "rules" map of rule IDs to
            firewall rule definitions referenced in the log entries
        """
        return await self.portal(
            "firewall/getEnterpriseFirewallLogs",
            request,
            GetEnterpriseFirewallLogsResult,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_logs_list(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallLogsList
    ) -> Sequence[GetEnterpriseFirewallLogsListItem]:
        """Get firewall logs for an enterprise from the VeloCloud Orchestrator.

        Retrieves the stateful firewall log entries recorded by SD-WAN Edges in the specified
        enterprise. Firewall logs capture traffic flow decisions made by the VeloCloud Enhanced
        Firewall, including allowed, denied, and dropped connections. Each log entry typically
        includes source/destination IP and port, protocol, the firewall rule that matched, the
        action taken (allow/deny/drop), byte and packet counts, and timestamps. These logs are
        essential for security auditing, troubleshooting connectivity issues caused by firewall
        policies, and verifying that firewall rules are working as intended. Optionally filter
        by a specific Edge ID to see logs from a single site.

        Returns:
            Object containing a "data" array of firewall log entries (each with source/dest
            IP, port, protocol, action, matching rule info, byte/packet counts, timestamp),
            a "metaData" object with pagination info, and a "rules" map of rule IDs to
            firewall rule definitions referenced in the log entries
        """
        return await self.portal_paginated_list(
            "firewall/getEnterpriseFirewallLogsList",
            request,
            GetEnterpriseFirewallLogsListItem,
        )

    @domain_tag(SECURITY)
    async def get_enterprise_firewall_rules(
        self: VcoClientBaseProtocol, request: GetEnterpriseFirewallRules
    ) -> GetEnterpriseFirewallRulesResult:
        """Get the configured firewall rules for an enterprise.

        Retrieves the complete set of firewall rules defined for the specified enterprise in the
        VeloCloud Orchestrator. Firewall rules control traffic flow through SD-WAN Edges and can
        be of type INBOUND (traffic entering the Edge from the WAN), OUTBOUND (traffic leaving
        the Edge toward the WAN), or SERVICE (traffic destined for Edge-hosted services). Each
        rule specifies match criteria (source/destination IP, port, protocol, application) and
        an action (allow, deny, drop). Rules are evaluated in priority order. Use this to audit
        the current security posture or to understand why certain traffic is being blocked or
        allowed by the SD-WAN firewall.

        Returns:
            Object containing a "rules" array of firewall rule objects, each with fields
            including name, type (INBOUND/OUTBOUND/SERVICE), match criteria, action, and
            rule ordering/priority information
        """
        return await self.portal(
            "firewall/getEnterpriseFirewallRules",
            request,
            GetEnterpriseFirewallRulesResult,
        )

    @domain_tag(SECURITY)
    async def get_malicious_ip_categories(
        self: VcoClientBaseProtocol, request: GetMaliciousIpCategories
    ) -> GetMaliciousIpCategoriesResult:
        """Get the industry-standard malicious IP category list for threat classification.

        Retrieves the mapping of category IDs to category names and groups used for classifying
        malicious IP addresses in the VeloCloud Enhanced Firewall's IP reputation feature. These
        categories represent well-known threat types (e.g., botnets, malware distribution,
        phishing, spam sources, scanners, tor exit nodes) and are used by the firewall to
        identify and block traffic to/from known malicious IP addresses. Use this to understand
        the available threat categories when configuring IP reputation-based firewall policies
        or when interpreting firewall log entries that reference malicious IP detections.

        Returns:
            Array or object mapping category IDs to their display names and group
            classifications for malicious IP threat categories
        """
        return await self.portal(
            "firewall/getMaliciousIpCategories",
            request,
            GetMaliciousIpCategoriesResult,
        )

    @domain_tag(SECURITY)
    async def get_url_filtering_categories(
        self: VcoClientBaseProtocol, request: GetUrlFilteringCategories
    ) -> GetUrlFilteringCategoriesResult:
        """Get the industry-standard URL filtering category list for web content classification.

        Retrieves the mapping of category IDs to category names and groups used for URL
        reputation and web content filtering in the VeloCloud Enhanced Firewall. URL filtering
        categories classify websites by content type (e.g., social media, gambling, adult
        content, streaming media, business applications) and are used to enforce acceptable
        use policies. These categories enable administrators to allow or block access to
        specific types of web content across the SD-WAN network. Use this to understand the
        available URL categories when configuring web filtering policies or interpreting
        firewall logs related to URL-based filtering decisions.

        Returns:
            Array or object mapping category IDs to their display names and group
            classifications for URL reputation categories
        """
        return await self.portal(
            "firewall/getUrlFilteringCategories",
            request,
            GetUrlFilteringCategoriesResult,
        )
