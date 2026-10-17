from __future__ import annotations

from typing import Any

from pydantic_core import MISSING

from pyvelo.analytics._helpers import items_to_frame
from pyvelo.analytics._series import denormalize_timeseries
from pyvelo.analytics.enums import EnterpriseFirewallType, FirewallType
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.metrics import (
    EnterpriseFirewallEdgeSummaryMetric,
    EnterpriseFirewallIdpsMetric,
    FirewallIdpsMetric,
    FirewallIdpsSeriesViewBy,
    FirewallMaliciousIpMetric,
    FirewallUrlCategoryMetric,
    FirewallUrlReputationMetric,
)
from pyvelo.client.schemas.metrics import (
    GetEdgeFirewallIdpsMetrics,
    GetEdgeFirewallIdpsSeries,
    GetEdgeFirewallMaliciousIpMetrics,
    GetEdgeFirewallUrlCategoryMetrics,
    GetEdgeFirewallUrlReputationMetrics,
    GetEnterpriseFirewallEdgeCountMetrics,
    GetEnterpriseFirewallEdgeSummaryMetrics,
    GetEnterpriseFirewallIdpsMetrics,
    GetEnterpriseFirewallMaliciousIpMetrics,
    GetEnterpriseFirewallUrlCategoryMetrics,
    GetEnterpriseFirewallUrlReputationMetrics,
)
from pyvelo.core import ApiInterval

if __builtins__:  # type: ignore[truthy-bool]
    from pyvelo.client.protocol import VcoClientProtocol


async def get_edge_firewall_metrics(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    type: FirewallType,
    view_by: str,
    *,
    enterprise_id: int | None = None,
    limit: int | None = None,
    sort: str | None = None,
) -> MetricFrame:
    limit_val = limit if limit is not None else MISSING
    eid = enterprise_id if enterprise_id is not None else MISSING

    match type:
        case FirewallType.IDPS:
            from pyvelo.client.enums.metrics import FirewallIdpsViewBy

            request = GetEdgeFirewallIdpsMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                view_by=FirewallIdpsViewBy(view_by),
                limit=limit_val,
                sort=FirewallIdpsMetric(sort) if sort else MISSING,
                metrics=list(FirewallIdpsMetric),
            )
            result = await client.get_edge_firewall_idps_metrics(request)
            return items_to_frame(result, _IDPS_FIELDS)

        case FirewallType.MALICIOUS_IP:
            from pyvelo.client.enums.metrics import FirewallMaliciousIpViewBy

            request = GetEdgeFirewallMaliciousIpMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                view_by=FirewallMaliciousIpViewBy(view_by),
                metrics=list(FirewallMaliciousIpMetric),
                limit=limit_val,
                sort=FirewallMaliciousIpMetric(sort) if sort else MISSING,
            )
            result = await client.get_edge_firewall_malicious_ip_metrics(request)
            return items_to_frame(result, _MAL_IP_FIELDS)

        case FirewallType.URL_CATEGORY:
            from pyvelo.client.enums.metrics import FirewallUrlCategoryViewBy

            request = GetEdgeFirewallUrlCategoryMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                view_by=FirewallUrlCategoryViewBy(view_by),
                limit=limit_val,
                sort=FirewallUrlCategoryMetric(sort) if sort else MISSING,
                metrics=list(FirewallUrlCategoryMetric),
            )
            result = await client.get_edge_firewall_url_category_metrics(request)
            return items_to_frame(result, _URL_CAT_FIELDS)

        case FirewallType.URL_REPUTATION:
            from pyvelo.client.enums.metrics import FirewallUrlReputationViewBy

            request = GetEdgeFirewallUrlReputationMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                view_by=FirewallUrlReputationViewBy(view_by),
                limit=limit_val,
                sort=FirewallUrlReputationMetric(sort) if sort else MISSING,
                metrics=list(FirewallUrlReputationMetric),
            )
            result = await client.get_edge_firewall_url_reputation_metrics(request)
            return items_to_frame(result, _URL_REP_FIELDS)

    raise ValueError(f"Unknown firewall type: {type}")


async def get_edge_firewall_series(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    max_samples: int,
    *,
    enterprise_id: int | None = None,
) -> MetricFrame:
    request = GetEdgeFirewallIdpsSeries(
        edge_id=edge_id,
        interval=interval,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
        view_by=FirewallIdpsSeriesViewBy.THREAT_IMPACT_ALIAS,
        max_samples=max_samples,
        metrics=list(FirewallIdpsMetric),
    )
    result = await client.get_edge_firewall_idps_series(request)
    return denormalize_timeseries(list(result), "name")


async def get_enterprise_firewall_metrics(
    client: VcoClientProtocol,
    interval: ApiInterval,
    type: EnterpriseFirewallType,
    view_by: str | None = None,
    *,
    enterprise_id: int | None = None,
    limit: int | None = None,
    sort: str | None = None,
) -> MetricFrame:
    limit_val = limit if limit is not None else MISSING
    eid = enterprise_id if enterprise_id is not None else MISSING

    match type:
        case EnterpriseFirewallType.IDPS:
            from pyvelo.client.enums.metrics import EnterpriseFirewallIdpsViewBy

            request = GetEnterpriseFirewallIdpsMetrics(
                interval=interval,
                enterprise_id=eid,
                view_by=(
                    EnterpriseFirewallIdpsViewBy(view_by)
                    if view_by
                    else EnterpriseFirewallIdpsViewBy.SIGNATURE_NAME
                ),
                limit=limit_val,
                sort=EnterpriseFirewallIdpsMetric(sort) if sort else MISSING,
                metrics=list(EnterpriseFirewallIdpsMetric),
            )
            result = await client.get_enterprise_firewall_idps_metrics(request)
            return items_to_frame(result, _IDPS_FIELDS)

        case EnterpriseFirewallType.EDGE_COUNT:
            request = GetEnterpriseFirewallEdgeCountMetrics(
                interval=interval, enterprise_id=eid
            )
            result = await client.get_enterprise_firewall_edge_count_metrics(request)
            row: dict[str, Any] = {}
            for field in ("total_edges_count", "impacted_edges_count"):
                val = getattr(result, field, None)
                if val is not MISSING:
                    row[field] = val
            cols = ["total_edges_count", "impacted_edges_count"]
            return MetricFrame(columns=cols, rows=[row] if row else [])

        case EnterpriseFirewallType.EDGE_SUMMARY:
            from pyvelo.client.enums.metrics import (
                EnterpriseFirewallEdgeSummarySortMetric,
            )

            request = GetEnterpriseFirewallEdgeSummaryMetrics(
                interval=interval,
                enterprise_id=eid,
                metrics=list(EnterpriseFirewallEdgeSummaryMetric),
                limit=limit_val,
                sort=EnterpriseFirewallEdgeSummarySortMetric(sort) if sort else MISSING,
            )
            result = await client.get_enterprise_firewall_edge_summary_metrics(request)
            return items_to_frame(result, _EDGE_SUMMARY_FIELDS)

        case EnterpriseFirewallType.MALICIOUS_IP:
            from pyvelo.client.enums.metrics import EnterpriseFirewallMaliciousIpViewBy

            request = GetEnterpriseFirewallMaliciousIpMetrics(
                interval=interval,
                enterprise_id=eid,
                view_by=(
                    EnterpriseFirewallMaliciousIpViewBy(view_by)
                    if view_by
                    else EnterpriseFirewallMaliciousIpViewBy.DEST_IP
                ),
                metrics=list(FirewallMaliciousIpMetric),
                limit=limit_val,
                sort=FirewallMaliciousIpMetric(sort) if sort else MISSING,
            )
            result = await client.get_enterprise_firewall_malicious_ip_metrics(request)
            return items_to_frame(result, _MAL_IP_FIELDS)

        case EnterpriseFirewallType.URL_CATEGORY:
            from pyvelo.client.enums.metrics import EnterpriseFirewallUrlCategoryViewBy

            request = GetEnterpriseFirewallUrlCategoryMetrics(
                interval=interval,
                enterprise_id=eid,
                view_by=(
                    EnterpriseFirewallUrlCategoryViewBy(view_by)
                    if view_by
                    else EnterpriseFirewallUrlCategoryViewBy.URL_CATEGORY
                ),
                limit=limit_val,
                sort=FirewallUrlCategoryMetric(sort) if sort else MISSING,
                metrics=list(FirewallUrlCategoryMetric),
            )
            result = await client.get_enterprise_firewall_url_category_metrics(request)
            return items_to_frame(result, _URL_CAT_FIELDS)

        case EnterpriseFirewallType.URL_REPUTATION:
            from pyvelo.client.enums.metrics import (
                EnterpriseFirewallUrlReputationViewBy,
            )

            request = GetEnterpriseFirewallUrlReputationMetrics(
                interval=interval,
                enterprise_id=eid,
                view_by=(
                    EnterpriseFirewallUrlReputationViewBy(view_by)
                    if view_by
                    else EnterpriseFirewallUrlReputationViewBy.DOMAIN_NAME
                ),
                limit=limit_val,
                sort=FirewallUrlReputationMetric(sort) if sort else MISSING,
                metrics=list(FirewallUrlReputationMetric),
            )
            result = await client.get_enterprise_firewall_url_reputation_metrics(
                request
            )
            return items_to_frame(result, _URL_REP_FIELDS)

    raise ValueError(f"Unknown enterprise firewall type: {type}")


_IDPS_FIELDS = [
    "name",
    "threats_count",
    "critical_threats_count",
    "major_threats_count",
    "minor_threats_count",
    "signature_id",
    "signature_category",
    "threat_impact",
]

_MAL_IP_FIELDS = [
    "name",
    "threats_count",
    "monitored_threats_count",
    "blocked_threats_count",
]

_URL_CAT_FIELDS = [
    "name",
    "threats_count",
    "urls_blocked_count",
    "urls_monitored_count",
    "urls_allowed_count",
]

_URL_REP_FIELDS = [
    "name",
    "threats_count",
    "urls_high_risk_count",
    "urls_medium_risk_count",
    "urls_low_risk_count",
    "urls_trustworthy_count",
    "urls_suspicious_count",
    "urls_blocked_count",
    "urls_monitored_count",
    "urls_allowed_count",
]

_EDGE_SUMMARY_FIELDS = [
    "name",
    "edge_logical_id",
    "edge_id",
    "idps_actions",
    "url_cat_actions",
    "url_rep_actions",
    "mal_ip_actions",
    "total_actions",
]
