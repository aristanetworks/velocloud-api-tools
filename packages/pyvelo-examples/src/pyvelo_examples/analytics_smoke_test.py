"""Smoke-test every Analytics endpoint against a real VCO.

Discovers context (edge, gateway, peers, NSD, links) then calls each method,
catches and classifies errors, and prints a pass/fail summary table.

Usage:
    uv run --package pyvelo-examples python -m pyvelo_examples.analytics_smoke_test \
        --env .credentials/lab.env --enterprise-id 123 --hours 72
"""

import argparse
import asyncio
import logging
import re
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import ValidationError
from pydantic_core import MISSING
from pyvelo.analytics import Analytics, FlowGroupBy, FlowMetric
from pyvelo.analytics.enums import EnterpriseFirewallType, FirewallType
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client import VcoClient
from pyvelo.client.enums import EdgeState
from pyvelo.client.enums.edge import GetEdgeWith, SdwanPeerType
from pyvelo.client.enums.metrics import BasicMetricViewBy
from pyvelo.client.exceptions import VcoError, VcoOperationError
from pyvelo.client.schemas.edge import (
    GetEdge,
    GetEdgeGatewayAssignmentsView,
    GetEdgeSdwanPeers,
)
from pyvelo.client.schemas.enterprise import (
    GetEnterprise,
    GetEnterpriseEdges,
    GetEnterpriseServices,
)
from pyvelo.client.schemas.metrics import LinkPair
from pyvelo.core import ApiInterval

from pyvelo_examples.common import get_vco_credentials

logger = logging.getLogger(__name__)

MIN_INTERFACE_STATS_VERSION = (6, 4, 0, 0)


class Status(StrEnum):
    PASS = "PASS"
    EMPTY = "EMPTY"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class TestResult:
    name: str
    status: Status
    rows: int = 0
    detail: str = ""


@dataclass
class DiscoveredContext:
    edge_id: int = 0
    edge_name: str = ""
    edge_version: str = ""
    gateway_id: int | None = None
    peer_logical_id: uuid.UUID | None = None
    link_logical_ids: list[uuid.UUID] = field(default_factory=list)
    nsd_logical_id: uuid.UUID | None = None


@dataclass
class AnalyticsSmokeTestArgs:
    env: str | None = None
    enterprise_id: int | None = None
    hours: int = 72
    edge_name: str | None = None
    verbose: bool = False
    dump_dir: str | None = None


def parse_args(argv: Sequence[str] | None = None) -> AnalyticsSmokeTestArgs:
    parser = argparse.ArgumentParser(description="Analytics smoke test")
    _ = parser.add_argument("--env", type=str, help="Path to .env file")
    _ = parser.add_argument("--enterprise-id", type=int)
    _ = parser.add_argument(
        "--hours", type=int, default=72, help="Lookback window (default: 72)"
    )
    _ = parser.add_argument(
        "--edge-name",
        type=str,
        help="Filter to edge matching this substring",
    )
    _ = parser.add_argument("--verbose", "-v", action="store_true")
    _ = parser.add_argument(
        "--dump-dir",
        type=str,
        help="Write raw VCO request/response bodies (sensitive) to this directory",
    )
    return parser.parse_args(argv, namespace=AnalyticsSmokeTestArgs())


def make_interval(hours: int) -> ApiInterval:
    now = datetime.now(tz=UTC)
    return ApiInterval(start=now - timedelta(hours=hours))


def _parse_version(version_str: str) -> tuple[int, ...]:
    match = re.match(r"(\d+(?:\.\d+)*)", version_str)
    if not match:
        return (0,)
    return tuple(int(p) for p in match.group(1).split("."))


def classify_error(exc: Exception) -> str:
    if isinstance(exc, VcoOperationError):
        return "API request failed"
    if isinstance(exc, VcoError):
        return "API request failed"
    if isinstance(exc, ValidationError):
        return f"Validation: {exc.error_count()} errors in {exc.title}"
    if isinstance(exc, TypeError | AttributeError):
        return f"Code error ({type(exc).__name__})"
    return type(exc).__name__


def _log_validation_detail(name: str, exc: ValidationError) -> None:
    logger.warning(
        "%s response failed schema validation (%d errors)", name, exc.error_count()
    )


async def run_test(
    name: str, fn: Callable[[], Awaitable[MetricFrame]]
) -> TestResult:
    try:
        frame: MetricFrame = await fn()
        if len(frame) > 0:
            return TestResult(name, Status.PASS, rows=len(frame))
        return TestResult(name, Status.EMPTY, rows=0)
    except Exception as exc:  # noqa: BLE001 - classify failures and continue the endpoint sweep
        logger.debug("FAIL %s (%s)", name, type(exc).__name__)
        if isinstance(exc, ValidationError):
            _log_validation_detail(name, exc)
        return TestResult(name, Status.FAIL, detail=classify_error(exc))


async def discover_context(
    client: VcoClient,
    enterprise_id: int,
    edge_name_filter: str | None,
    interval: ApiInterval,
) -> DiscoveredContext:
    ctx = DiscoveredContext()

    edges = await client.get_enterprise_edges(
        GetEnterpriseEdges(enterprise_id=enterprise_id)
    )
    connected = [
        e
        for e in edges
        if e.edge_state == EdgeState.CONNECTED and isinstance(e.id_, int)
    ]
    if edge_name_filter is not None:
        connected = [
            e
            for e in connected
            if isinstance(e.name, str) and edge_name_filter.lower() in e.name.lower()
        ]

    if not connected:
        logger.error("No connected edges found")
        return ctx

    # Prefer an edge running >= 6.4.0.0 for interface stats coverage
    best = connected[0]
    for candidate in connected:
        ver_str = candidate.software_version
        if ver_str and ver_str is not MISSING:
            ver = _parse_version(ver_str)
            if ver >= MIN_INTERFACE_STATS_VERSION:
                best = candidate
                break

    if not isinstance(best.id_, int):
        return ctx
    ctx.edge_id = best.id_
    ctx.edge_name = (
        best.name
        if isinstance(best.name, str) and best.name
        else f"edge-{ctx.edge_id}"
    )
    ver = best.software_version
    ctx.edge_version = ver if ver and ver is not MISSING else "unknown"
    logger.info(
        "Selected edge: %s (ID %d, version %s)",
        ctx.edge_name,
        ctx.edge_id,
        ctx.edge_version,
    )

    # Links
    try:
        edge_detail = await client.get_edge(
            GetEdge(
                id_=ctx.edge_id, enterprise_id=enterprise_id, with_=[GetEdgeWith.LINKS]
            )
        )
        if edge_detail.links and edge_detail.links is not MISSING:
            for link in edge_detail.links:
                if isinstance(link.logical_id, str):
                    try:
                        logical_id = uuid.UUID(link.logical_id)
                    except ValueError:
                        continue
                    ctx.link_logical_ids.append(logical_id)
            logger.info("Discovered %d links", len(ctx.link_logical_ids))
    except Exception as exc:  # noqa: BLE001 - continue discovery after optional data failures
        logger.warning("Link discovery failed (%s)", type(exc).__name__)

    # SD-WAN peers
    try:
        peers = await client.get_edge_sdwan_peers(
            GetEdgeSdwanPeers(
                edge_id=ctx.edge_id,
                enterprise_id=enterprise_id,
                interval=interval,
            )
        )
        for peer in peers:
            if peer.peer_type != SdwanPeerType.GATEWAY:
                ctx.peer_logical_id = peer.device_logical_id
                break
        if ctx.peer_logical_id:
            logger.info("Discovered peer: %s", ctx.peer_logical_id)
        else:
            logger.info("No non-gateway SD-WAN peers found")
    except Exception as exc:  # noqa: BLE001 - continue discovery after optional data failures
        logger.warning("Peer discovery failed (%s)", type(exc).__name__)

    # Gateway
    try:
        gw_view = await client.get_edge_gateway_assignment_view(
            GetEdgeGatewayAssignmentsView(id_=ctx.edge_id, enterprise_id=enterprise_id)
        )
        if (
            gw_view.primary
            and gw_view.primary is not MISSING
            and gw_view.primary.id_
            and gw_view.primary.id_ is not MISSING
        ):
            ctx.gateway_id = gw_view.primary.id_
            logger.info("Discovered gateway: %d", ctx.gateway_id)
    except Exception as exc:  # noqa: BLE001 - continue discovery after optional data failures
        logger.warning("Gateway discovery failed (%s)", type(exc).__name__)

    # NSD (NVS via Edge services)
    try:
        services = await client.get_enterprise_services(
            GetEnterpriseServices(enterprise_id=enterprise_id)
        )
        for svc in services:
            svc_type = getattr(svc, "type_", None)
            if svc_type == "nvsViaEdgeService":
                ctx.nsd_logical_id = svc.logical_id
                logger.info("Discovered NSD service: %s", ctx.nsd_logical_id)
                break
    except Exception as exc:  # noqa: BLE001 - continue discovery after optional data failures
        logger.warning("NSD discovery failed (%s)", type(exc).__name__)

    return ctx


async def run_all_tests(
    analytics: Analytics,
    interval: ApiInterval,
    ctx: DiscoveredContext,
) -> list[TestResult]:
    results: list[TestResult] = []

    edge_id = ctx.edge_id

    # --- Edge flow metrics (all group-by variants) ---
    for gb in FlowGroupBy:
        results.append(
            await run_test(
                f"edge_flow_metrics({gb.name})",
                lambda gb=gb: analytics.get_edge_flow_metrics(
                    edge_id,
                    interval,
                    gb,
                    limit=5,
                    sort=FlowMetric.TOTAL_BYTES,
                ),
            )
        )

    # --- Edge flow series (all group-by variants) ---
    for gb in FlowGroupBy:
        results.append(
            await run_test(
                f"edge_flow_series({gb.name})",
                lambda gb=gb: analytics.get_edge_flow_series(
                    edge_id,
                    interval,
                    gb,
                    limit=5,
                    sort=FlowMetric.TOTAL_BYTES,
                ),
            )
        )

    # --- Enterprise flow metrics (all view-by variants) ---
    for vb in BasicMetricViewBy:
        results.append(
            await run_test(
                f"enterprise_flow_metrics({vb.name})",
                lambda vb=vb: analytics.get_enterprise_flow_metrics(interval, vb),
            )
        )

    # --- Edge link metrics + series ---
    results.append(
        await run_test(
            "edge_link_metrics",
            lambda: analytics.get_edge_link_metrics(edge_id, interval),
        )
    )
    results.append(
        await run_test(
            "edge_link_series",
            lambda: analytics.get_edge_link_series(edge_id, interval),
        )
    )

    # --- Aggregate link metrics ---
    results.append(
        await run_test(
            "aggregate_link_metrics",
            lambda: analytics.get_aggregate_link_metrics(interval),
        )
    )

    # --- Edge interface metrics + series ---
    if _parse_version(ctx.edge_version) >= MIN_INTERFACE_STATS_VERSION:
        results.append(
            await run_test(
                "edge_interface_metrics",
                lambda: analytics.get_edge_interface_metrics(edge_id, interval),
            )
        )
        results.append(
            await run_test(
                "edge_interface_series",
                lambda: analytics.get_edge_interface_series(edge_id, interval),
            )
        )
    else:
        detail = f"Edge version {ctx.edge_version} < 6.4.0.0"
        results.append(TestResult("edge_interface_metrics", Status.SKIP, detail=detail))
        results.append(TestResult("edge_interface_series", Status.SKIP, detail=detail))

    # --- Edge path metrics + series (needs peer + links) ---
    if ctx.peer_logical_id and len(ctx.link_logical_ids) >= 1:
        peer_id = ctx.peer_logical_id
        paths = [LinkPair(src_link_logical_id=lid) for lid in ctx.link_logical_ids[:2]]
        results.append(
            await run_test(
                "edge_path_metrics",
                lambda: analytics.get_edge_path_metrics(edge_id, interval, peer_id),
            )
        )
        results.append(
            await run_test(
                "edge_path_series",
                lambda: analytics.get_edge_path_series(
                    edge_id, interval, peer_id, paths
                ),
            )
        )
    else:
        results.append(
            TestResult("edge_path_metrics", Status.SKIP, detail="No SD-WAN peers")
        )
        results.append(
            TestResult("edge_path_series", Status.SKIP, detail="No SD-WAN peers")
        )

    # --- NSD tunnel metrics ---
    if ctx.nsd_logical_id:
        nsd_id = ctx.nsd_logical_id
        results.append(
            await run_test(
                "nsd_tunnel_metrics",
                lambda: analytics.get_nsd_tunnel_metrics(nsd_id, interval),
            )
        )
    else:
        results.append(
            TestResult("nsd_tunnel_metrics", Status.SKIP, detail="No NSD service")
        )

    # --- Edge firewall metrics (all type/view combos) ---
    _EDGE_FW_VIEWS: list[tuple[FirewallType, str]] = [
        (FirewallType.IDPS, "signatureName"),
        (FirewallType.IDPS, "threatImpactAlias"),
        (FirewallType.IDPS, "alert"),
        (FirewallType.IDPS, "threatSourceIp"),
        (FirewallType.IDPS, "threatTargetIp"),
        (FirewallType.IDPS, "threatSourceGeoCountry"),
        (FirewallType.MALICIOUS_IP, "destIp"),
        (FirewallType.MALICIOUS_IP, "action"),
        (FirewallType.MALICIOUS_IP, "ipCategory"),
        (FirewallType.MALICIOUS_IP, "sourceIp"),
        (FirewallType.MALICIOUS_IP, "threatSourceGeoCountry"),
        (FirewallType.URL_CATEGORY, "urlCategory"),
        (FirewallType.URL_CATEGORY, "action"),
        (FirewallType.URL_CATEGORY, "sourceIp"),
        (FirewallType.URL_REPUTATION, "domainName"),
        (FirewallType.URL_REPUTATION, "sourceIp"),
        (FirewallType.URL_REPUTATION, "urlRisk"),
        (FirewallType.URL_REPUTATION, "action"),
    ]
    for fw_type, view_by in _EDGE_FW_VIEWS:
        results.append(
            await run_test(
                f"edge_fw({fw_type.name},{view_by})",
                lambda ft=fw_type, vb=view_by: analytics.get_edge_firewall_metrics(
                    edge_id,
                    interval,
                    ft,
                    view_by=vb,
                ),
            )
        )

    # --- Edge firewall series ---
    results.append(
        await run_test(
            "edge_firewall_series",
            lambda: analytics.get_edge_firewall_series(
                edge_id, interval, max_samples=10
            ),
        )
    )

    # --- Enterprise firewall metrics (all type/view combos) ---
    _ENT_FW_VIEWS: list[tuple[EnterpriseFirewallType, str | None]] = [
        (EnterpriseFirewallType.IDPS, "signatureName"),
        (EnterpriseFirewallType.IDPS, "threatImpactAlias"),
        (EnterpriseFirewallType.IDPS, "alert"),
        (EnterpriseFirewallType.IDPS, "threatSourceIp"),
        (EnterpriseFirewallType.IDPS, "threatTargetIp"),
        (EnterpriseFirewallType.IDPS, "threatSourceGeoCountry"),
        (EnterpriseFirewallType.IDPS, "edgeLogicalId"),
        (EnterpriseFirewallType.EDGE_COUNT, None),
        (EnterpriseFirewallType.EDGE_SUMMARY, None),
        (EnterpriseFirewallType.MALICIOUS_IP, "destIp"),
        (EnterpriseFirewallType.MALICIOUS_IP, "action"),
        (EnterpriseFirewallType.MALICIOUS_IP, "ipCategory"),
        (EnterpriseFirewallType.MALICIOUS_IP, "sourceIp"),
        (EnterpriseFirewallType.MALICIOUS_IP, "threatSourceGeoCountry"),
        (EnterpriseFirewallType.MALICIOUS_IP, "edgeLogicalId"),
        (EnterpriseFirewallType.URL_CATEGORY, "urlCategory"),
        (EnterpriseFirewallType.URL_CATEGORY, "action"),
        (EnterpriseFirewallType.URL_CATEGORY, "sourceIp"),
        (EnterpriseFirewallType.URL_CATEGORY, "edgeLogicalId"),
        (EnterpriseFirewallType.URL_REPUTATION, "domainName"),
        (EnterpriseFirewallType.URL_REPUTATION, "sourceIp"),
        (EnterpriseFirewallType.URL_REPUTATION, "urlRisk"),
        (EnterpriseFirewallType.URL_REPUTATION, "action"),
        (EnterpriseFirewallType.URL_REPUTATION, "edgeLogicalId"),
    ]
    for ent_type, view_by in _ENT_FW_VIEWS:
        label = f"{ent_type.name}" + (f",{view_by}" if view_by else "")
        results.append(
            await run_test(
                f"ent_fw({label})",
                lambda et=ent_type, vb=view_by: (
                    analytics.get_enterprise_firewall_metrics(
                        interval,
                        et,
                        vb,
                    )
                ),
            )
        )

    # --- Edge health metrics + series ---
    results.append(
        await run_test(
            "edge_health_metrics",
            lambda: analytics.get_edge_health_metrics(edge_id, interval),
        )
    )
    results.append(
        await run_test(
            "edge_health_series",
            lambda: analytics.get_edge_health_series(edge_id, interval),
        )
    )

    # --- Gateway health metrics + series ---
    if ctx.gateway_id:
        gw_id = ctx.gateway_id
        results.append(
            await run_test(
                "gateway_health_metrics",
                lambda: analytics.get_gateway_health_metrics(gw_id, interval),
            )
        )
        results.append(
            await run_test(
                "gateway_health_series",
                lambda: analytics.get_gateway_health_series(gw_id, interval),
            )
        )
    else:
        results.append(
            TestResult("gateway_health_metrics", Status.SKIP, detail="No gateway")
        )
        results.append(
            TestResult("gateway_health_series", Status.SKIP, detail="No gateway")
        )

    # --- QoE series ---
    results.append(
        await run_test(
            "edge_qoe_series",
            lambda: analytics.get_edge_qoe_series(edge_id, interval),
        )
    )

    # --- NNI metrics + series ---
    results.append(
        await run_test(
            "nni_metrics",
            lambda: analytics.get_nni_metrics(interval),
        )
    )
    results.append(
        await run_test(
            "nni_series",
            lambda: analytics.get_nni_series(interval),
        )
    )

    return results


def print_results(
    results: list[TestResult], enterprise_id: int, hours: int, ctx: DiscoveredContext
) -> None:
    header = f"Analytics Smoke Test — enterprise {enterprise_id}, {hours}h, edge {ctx.edge_name} ({ctx.edge_id}, {ctx.edge_version})"
    sep = "=" * max(len(header), 78)

    print(f"\n{header}")
    print(sep)
    print(f"{'#':>3}  {'Test':<48} {'Status':<6} {'Rows':>5}  Detail")

    for i, r in enumerate(results, 1):
        rows_str = str(r.rows) if r.status in (Status.PASS, Status.EMPTY) else "-"
        print(f"{i:>3}  {r.name:<48} {r.status:<6} {rows_str:>5}  {r.detail}")

    print(sep)
    counts = {s: sum(1 for r in results if r.status == s) for s in Status}
    summary = ", ".join(f"{counts[s]} {s}" for s in Status if counts[s])
    print(f"Summary: {summary}")


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    vco_host, vco_token = get_vco_credentials(args.env)
    interval = make_interval(args.hours)

    async with VcoClient(vco_host, vco_token) as client:
        if args.dump_dir:
            client.enable_dump(args.dump_dir)
            logger.warning(
                "Writing raw VCO request/response bodies to %s; protect or delete these files after use",
                args.dump_dir,
            )

        enterprise_id = args.enterprise_id
        if enterprise_id is None:
            ent = await client.get_enterprise(GetEnterprise())
            enterprise_id = ent.id_
            logger.info(
                "Auto-discovered enterprise: %s (ID %d)", ent.name, enterprise_id
            )

        ctx = await discover_context(client, enterprise_id, args.edge_name, interval)
        if not ctx.edge_id:
            print("FATAL: No edge discovered, cannot run tests")
            sys.exit(1)

        analytics = Analytics(client, enterprise_id=enterprise_id)
        results = await run_all_tests(analytics, interval, ctx)

    print_results(results, enterprise_id, args.hours, ctx)

    if any(r.status == Status.FAIL for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
