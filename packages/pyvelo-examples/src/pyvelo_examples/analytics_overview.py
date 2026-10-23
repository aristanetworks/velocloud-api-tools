"""Fetch analytics metrics for all edges in an enterprise and display as Polars tables.

Discovers edges from the given enterprise-id, then pulls flow, health, link,
and firewall metrics for each connected edge over the last hour.

Usage:
    uv run --package pyvelo-examples python -m pyvelo_examples.analytics_overview --enterprise-id 123
    uv run --package pyvelo-examples python -m pyvelo_examples.analytics_overview --enterprise-id 123 --env .credentials/lab.env
    uv run --package pyvelo-examples python -m pyvelo_examples.analytics_overview --enterprise-id 123 --hours 24
"""

import argparse
import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import polars as pl
from pyvelo.analytics import Analytics, FlowGroupBy, FlowMetric
from pyvelo.analytics.enums import FirewallType
from pyvelo.client import VcoClient
from pyvelo.client.enums import EdgeState
from pyvelo.client.schemas.enterprise import GetEnterprise, GetEnterpriseEdges
from pyvelo.core import ApiInterval

from pyvelo_examples.common import get_vco_credentials

logger = logging.getLogger(__name__)

@dataclass
class AnalyticsOverviewArgs:
    env: str | None = None
    enterprise_id: int | None = None
    hours: int = 1
    edge_name: str | None = None
    verbose: bool = False


def parse_args(argv: Sequence[str] | None = None) -> AnalyticsOverviewArgs:
    parser = argparse.ArgumentParser(
        description="Fetch analytics overview for all edges in an enterprise.",
    )
    _ = parser.add_argument("--env", type=str, help="Path to .env file")
    _ = parser.add_argument(
        "--enterprise-id",
        type=int,
        help="Enterprise ID (auto-discovered from token if omitted)",
    )
    _ = parser.add_argument(
        "--hours",
        type=int,
        help="Lookback window in hours (default: 1)",
    )
    _ = parser.add_argument(
        "--edge-name",
        type=str,
        help="Filter to edges matching this substring",
    )
    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args(argv, namespace=AnalyticsOverviewArgs())


def make_interval(hours: int) -> ApiInterval:
    now = datetime.now(tz=UTC)
    return ApiInterval(start=now - timedelta(hours=hours))


def print_frame(title: str, df: pl.DataFrame) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")
    if df.is_empty():
        print("  (no data)")
    else:
        print(df)
    print()


async def fetch_edge_metrics(
    analytics: Analytics,
    edge_id: int,
    edge_name: str,
    interval: ApiInterval,
) -> dict[str, pl.DataFrame]:
    frames: dict[str, pl.DataFrame] = {}

    # Flow metrics by application
    try:
        flow = await analytics.get_edge_flow_metrics(
            edge_id,
            interval,
            FlowGroupBy.APP,
            limit=10,
            sort=FlowMetric.TOTAL_BYTES,
        )
        frames[f"Top Applications — {edge_name}"] = flow.to_dataframe()
    except Exception as exc:  # noqa: BLE001 - continue collecting other endpoint results
        logger.warning("Flow metrics failed for %s (%s)", edge_name, type(exc).__name__)

    # Health metrics
    try:
        health = await analytics.get_edge_health_metrics(edge_id, interval)
        frames[f"Health — {edge_name}"] = health.to_dataframe()
    except Exception as exc:  # noqa: BLE001 - continue collecting other endpoint results
        logger.warning(
            "Health metrics failed for %s (%s)", edge_name, type(exc).__name__
        )

    # Link metrics
    try:
        links = await analytics.get_edge_link_metrics(edge_id, interval)
        frames[f"Link Metrics — {edge_name}"] = links.to_dataframe()
    except Exception as exc:  # noqa: BLE001 - continue collecting other endpoint results
        logger.warning("Link metrics failed for %s (%s)", edge_name, type(exc).__name__)

    # Firewall IDPS metrics
    try:
        fw = await analytics.get_edge_firewall_metrics(
            edge_id,
            interval,
            FirewallType.IDPS,
            view_by="signatureName",
        )
        frames[f"Firewall IDPS — {edge_name}"] = fw.to_dataframe()
    except Exception as exc:  # noqa: BLE001 - continue collecting other endpoint results
        logger.warning(
            "Firewall metrics failed for %s (%s)", edge_name, type(exc).__name__
        )

    return frames


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
        enterprise_id = args.enterprise_id
        if enterprise_id is None:
            ent = await client.get_enterprise(GetEnterprise())
            enterprise_id = ent.id_
            logger.info(f"Auto-discovered enterprise: {ent.name} (ID {enterprise_id})")

        logger.info(f"Fetching edges for enterprise {enterprise_id}...")
        edges = await client.get_enterprise_edges(
            GetEnterpriseEdges(enterprise_id=enterprise_id)
        )

        connected = [e for e in edges if e.edge_state == EdgeState.CONNECTED]
        if args.edge_name is not None:
            connected = [
                e
                for e in connected
                if isinstance(e.name, str) and args.edge_name.lower() in e.name.lower()
            ]

        if not connected:
            logger.warning("No connected edges found matching criteria")
            return

        logger.info(
            f"{len(connected)} connected edge(s): "
            + ", ".join(e.name or f"edge-{e.id_}" for e in connected)
        )

        # Enterprise-wide flow metrics
        analytics = Analytics(client, enterprise_id=enterprise_id)

        try:
            from pyvelo.client.enums.metrics import BasicMetricViewBy

            ent_flow = await analytics.get_enterprise_flow_metrics(
                interval,
                BasicMetricViewBy.APPLICATION,
            )
            print_frame(
                f"Enterprise Flow by Application (last {args.hours}h)",
                ent_flow.to_dataframe(),
            )
        except Exception as exc:  # noqa: BLE001 - continue collecting other endpoint results
            logger.warning("Enterprise flow metrics failed (%s)", type(exc).__name__)

        # Per-edge metrics
        for edge in connected:
            if not isinstance(edge.id_, int):
                continue
            edge_name = edge.name
            if not isinstance(edge_name, str) or not edge_name:
                edge_name = f"edge-{edge.id_}"
            logger.info(f"Fetching metrics for {edge_name} (ID {edge.id_})...")

            frames = await fetch_edge_metrics(
                analytics,
                edge.id_,
                edge_name,
                interval,
            )
            for title, df in frames.items():
                print_frame(title, df)

    print(f"Done — {vco_host}, enterprise {enterprise_id}, last {args.hours}h")


if __name__ == "__main__":
    asyncio.run(main())
