"""Get all BGP neighbors configured across edges in an enterprise.

Usage:
    uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors --enterprise-id 123
    uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors --enterprise-id 123 --format csv
    uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors --enterprise-id 123 --segment "Global Segment"
"""

import argparse
import asyncio
import csv
import io
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic_core import MISSING
from pyvelo.client import VcoClient
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges, GetEnterpriseEdgesItem
from rich.console import Console
from rich.table import Table
from rich.text import Text

from pyvelo_examples.common import get_vco_credentials

logger = logging.getLogger(__name__)


@dataclass
class BgpNeighborRow:
    edge_name: str
    edge_id: int
    segment_name: str
    neighbor_ip: str
    neighbor_as: int
    local_ip: str
    bfd: bool
    uplink: bool
    max_hop: int


@dataclass
class BgpNeighborArgs(argparse.Namespace):
    enterprise_id: int = field(init=False)
    env: str | None = None
    segment: str | None = None
    format: Literal["table", "csv", "json"] = "table"
    concurrency: int = 3
    verbose: bool = False


def parse_args(argv: Sequence[str] | None = None) -> BgpNeighborArgs:
    parser = argparse.ArgumentParser(
        description="Get all BGP neighbors configured across edges in an enterprise."
    )
    _ = parser.add_argument("--env", type=str, default=None, help="Path to .env file")
    _ = parser.add_argument(
        "--enterprise-id", type=int, required=True, help="Enterprise ID to query"
    )
    _ = parser.add_argument(
        "--segment", type=str, default=None, help="Filter to a specific segment name"
    )
    _ = parser.add_argument(
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    _ = parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent API requests (default: 3)",
    )
    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args(argv, namespace=BgpNeighborArgs())


def _string_or_default(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer_or_default(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _boolean_or_default(value: object, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


async def collect_bgp_neighbors(
    client: VcoClient,
    edge_id: int,
    edge_name: str,
    enterprise_id: int,
    segment_filter: str | None,
) -> list[BgpNeighborRow]:
    """Extract BGP neighbors from a single edge's device settings."""
    rows: list[BgpNeighborRow] = []
    try:
        stack = await client.get_edge_configuration_stack(
            GetEdgeConfigurationStack(edge_id=edge_id, enterprise_id=enterprise_id)  # type: ignore[arg-type]
        )
        edge_config, _profile = stack[0], stack[1]
        data = edge_config.device_settings.data

        for seg in data.segments:
            if segment_filter and seg.segment.name != segment_filter:
                continue

            bgp = seg.bgp
            if bgp is MISSING:
                continue
            if not bgp.enabled:
                continue

            neighbors = bgp.neighbors
            if neighbors is MISSING:
                continue

            for n in neighbors:
                rows.append(
                    BgpNeighborRow(
                        edge_name=edge_name,
                        edge_id=edge_id,
                        segment_name=_string_or_default(cast(object, seg.segment.name)),
                        neighbor_ip=_string_or_default(cast(object, n.neighbor_ip)),
                        neighbor_as=_integer_or_default(cast(object, n.neighbor_as)),
                        local_ip=_string_or_default(cast(object, n.local_ip)),
                        bfd=_boolean_or_default(cast(object, n.enable_bfd)),
                        uplink=_boolean_or_default(cast(object, n.uplink)),
                        max_hop=_integer_or_default(cast(object, n.max_hop), 1),
                    )
                )
    except Exception as e:  # noqa: BLE001 - continue collecting neighbors from other edges
        logger.warning(
            "Failed to get BGP config for edge %s (%s)", edge_name, type(e).__name__
        )
    return rows


def output_table(rows: list[BgpNeighborRow], enterprise_id: int, vco_host: str) -> None:
    """Render BGP neighbors as a rich table."""
    console = Console()
    table = Table(
        title=Text(f"BGP Neighbors — Enterprise {enterprise_id} ({vco_host})")
    )
    _ = table.add_column("Edge Name", style="cyan")
    _ = table.add_column("Edge ID", justify="right")
    _ = table.add_column("Segment")
    _ = table.add_column("Neighbor IP", style="green")
    _ = table.add_column("AS", justify="right")
    _ = table.add_column("Local IP")
    _ = table.add_column("BFD", justify="center")
    _ = table.add_column("Uplink", justify="center")
    _ = table.add_column("Max Hop", justify="right")

    for r in rows:
        table.add_row(
            Text(r.edge_name),
            str(r.edge_id),
            Text(r.segment_name),
            Text(r.neighbor_ip),
            str(r.neighbor_as),
            Text(r.local_ip),
            "Yes" if r.bfd else "No",
            "Yes" if r.uplink else "No",
            str(r.max_hop),
        )

    console.print(table)

    edge_count = len({r.edge_id for r in rows})
    segment_count = len({r.segment_name for r in rows})
    console.print(
        f"\nTotal: {len(rows)} BGP neighbors across {edge_count} edges ({segment_count} segments)"
    )


def output_csv(rows: list[BgpNeighborRow]) -> None:
    """Write BGP neighbors as CSV to stdout."""

    def safe_text(value: str) -> str:
        """Prevent spreadsheet software from evaluating VCO text as a formula."""
        if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "edge_name",
            "edge_id",
            "segment",
            "neighbor_ip",
            "neighbor_as",
            "local_ip",
            "bfd",
            "uplink",
            "max_hop",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                safe_text(r.edge_name),
                r.edge_id,
                safe_text(r.segment_name),
                safe_text(r.neighbor_ip),
                r.neighbor_as,
                safe_text(r.local_ip),
                r.bfd,
                r.uplink,
                r.max_hop,
            ]
        )
    _ = sys.stdout.write(buf.getvalue())


def output_json(rows: list[BgpNeighborRow]) -> None:
    """Write BGP neighbors as JSON to stdout."""
    data = [
        {
            "edge_name": r.edge_name,
            "edge_id": r.edge_id,
            "segment": r.segment_name,
            "neighbor_ip": r.neighbor_ip,
            "neighbor_as": r.neighbor_as,
            "local_ip": r.local_ip,
            "bfd": r.bfd,
            "uplink": r.uplink,
            "max_hop": r.max_hop,
        }
        for r in rows
    ]
    json.dump(data, sys.stdout, indent=2)
    _ = sys.stdout.write("\n")


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    vco_host, vco_token = get_vco_credentials(args.env)

    async with VcoClient(vco_host, vco_token) as client:
        logger.info(f"Fetching edges for enterprise {args.enterprise_id}...")
        request = GetEnterpriseEdges(enterprise_id=args.enterprise_id)
        edges = await client.get_enterprise_edges(request)
        logger.info(f"Found {len(edges)} edges")

        semaphore = asyncio.Semaphore(args.concurrency)
        all_rows: list[BgpNeighborRow] = []

        async def process_edge(edge: GetEnterpriseEdgesItem) -> list[BgpNeighborRow]:
            async with semaphore:
                idx = edges.index(edge) + 1
                edge_id = edge.id_
                if not isinstance(edge_id, int):
                    logger.warning("Skipping Edge with missing or invalid ID")
                    return []
                edge_name = edge.name
                if not isinstance(edge_name, str) or not edge_name:
                    edge_name = f"edge-{edge_id}"
                logger.debug("Processing edge %d/%d: %s...", idx, len(edges), edge_name)
                return await collect_bgp_neighbors(
                    client, edge_id, edge_name, args.enterprise_id, args.segment
                )

        tasks = [process_edge(edge) for edge in edges]
        results = await asyncio.gather(*tasks)
        for result in results:
            all_rows.extend(result)

    if not all_rows:
        logger.info("No BGP neighbors found")
        return

    if args.format == "table":
        output_table(all_rows, args.enterprise_id, vco_host)
    elif args.format == "csv":
        output_csv(all_rows)
    elif args.format == "json":
        output_json(all_rows)


if __name__ == "__main__":
    asyncio.run(main())
