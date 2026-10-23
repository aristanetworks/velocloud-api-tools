"""Run ARP_DUMP on all connected edges and display results in a table.

Usage:
    uv run --package pyvelo-examples python -m pyvelo_examples.run_arp_dump --env .credentials/lab.env
    uv run --package pyvelo-examples python -m pyvelo_examples.run_arp_dump --env .credentials/lab.env --enterprise-id 123
    uv run --package pyvelo-examples python -m pyvelo_examples.run_arp_dump --env .credentials/lab.env --edge-name "branch-01"
"""

import argparse
import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from pyvelo.client import VcoClient, VcoWebSocket, VcoWebSocketError
from pyvelo.client.enums import EdgeState
from pyvelo.client.schemas.enterprise import (
    GetEnterprise,
    GetEnterpriseEdges,
    GetEnterpriseEdgesItem,
)
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .common import get_vco_credentials

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ArpEntry:
    interface: str
    address: str
    mac: str
    state: str
    age: int  # seconds


@dataclass
class EdgeArpResult:
    edge_name: str
    entries: list[ArpEntry] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _format_age(seconds: int) -> str:
    """Format age in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60}m"


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return a JSON object as a read-only mapping, or an empty mapping."""
    if isinstance(value, dict):
        # JSON object keys are strings; `json.loads` guarantees that for parsed input.
        return cast(Mapping[str, object], value)
    return {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def parse_arp_json(data: object) -> list[ArpEntry]:
    """Extract ARP entries from the JSON diagnostic response.

    Response path:
        data["results"]["output"]  (JSON string)
          -> ARP_DUMP.result[0].entry[]
               -> { interface, isEmpty, row[] -> { address, mac, state, age } }
    """
    output_str = _as_mapping(_as_mapping(data).get("results")).get("output")
    if not isinstance(output_str, str) or not output_str:
        return []

    output = cast(object, json.loads(output_str))
    arp = _as_mapping(_as_mapping(output).get("ARP_DUMP"))

    entries: list[ArpEntry] = []
    for result_block in _as_list(arp.get("result")):
        for iface_group in _as_list(_as_mapping(result_block).get("entry")):
            iface_data = _as_mapping(iface_group)
            if iface_data.get("isEmpty", True):
                continue
            interface = _text(iface_data.get("interface"), "?")
            for row in _as_list(iface_data.get("row")):
                row_data = _as_mapping(row)
                entries.append(
                    ArpEntry(
                        interface=interface,
                        address=_text(row_data.get("address")),
                        mac=_text(row_data.get("mac")),
                        state=_text(row_data.get("state")),
                        age=_integer(row_data.get("age")),
                    )
                )
    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass
class ArpDumpArgs:
    """Typed argparse namespace and defaults for omitted options."""

    env: str | None = None
    enterprise_id: int | None = None
    edge_name: str | None = None
    concurrency: int = 3
    timeout: float = 120.0
    verbose: bool = False


def parse_args(argv: Sequence[str] | None = None) -> ArpDumpArgs:
    parser = argparse.ArgumentParser(
        description="Run ARP_DUMP on connected edges and display results."
    )
    _ = parser.add_argument("--env", type=str, help="Path to .env file")
    _ = parser.add_argument(
        "--enterprise-id",
        type=int,
        help="Enterprise ID (auto-discovered from token if omitted)",
    )
    _ = parser.add_argument(
        "--edge-name",
        type=str,
        help="Filter to a specific edge by name (substring match)",
    )
    _ = parser.add_argument(
        "--concurrency",
        type=int,
        help="Max concurrent diagnostic requests (default: 3)",
    )
    _ = parser.add_argument(
        "--timeout",
        type=float,
        help="Timeout per diagnostic in seconds (default: 120)",
    )
    _ = parser.add_argument(
        "--verbose", "-v", action="store_true", help="Debug logging"
    )
    return parser.parse_args(argv, namespace=ArpDumpArgs())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_results(results: list[EdgeArpResult], vco_host: str) -> None:
    console = Console()

    # Summary table.
    summary = Table(title=Text(f"ARP Dump Summary -- {vco_host}"))
    summary.add_column("Edge", style="cyan")
    summary.add_column("Entries", justify="right")
    summary.add_column("Status")

    for r in results:
        if r.error:
            summary.add_row(Text(r.edge_name), "-", Text(r.error, style="red"))
        else:
            summary.add_row(
                Text(r.edge_name), str(len(r.entries)), Text("OK", style="green")
            )

    console.print(summary)
    console.print()

    # Detail table.
    all_entries = [(r.edge_name, e) for r in results for e in r.entries]
    if not all_entries:
        console.print("[yellow]No ARP entries found.[/yellow]")
        return

    detail = Table(title=Text("ARP Entries"))
    detail.add_column("Edge", style="cyan")
    detail.add_column("Interface", style="blue")
    detail.add_column("IP Address", style="green")
    detail.add_column("MAC Address")
    detail.add_column("State")
    detail.add_column("Age", justify="right")

    for edge_name, entry in all_entries:
        state_style = "green" if entry.state == "ALIVE" else "red"
        detail.add_row(
            Text(edge_name),
            Text(entry.interface),
            Text(entry.address),
            Text(entry.mac),
            Text(entry.state, style=state_style),
            _format_age(entry.age),
        )

    console.print(detail)
    console.print(
        f"\nTotal: {len(all_entries)} ARP entries across {len(results)} edges"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    vco_host, vco_token = get_vco_credentials(args.env)

    async with VcoClient(vco_host, vco_token) as client:
        # Resolve enterprise ID.
        enterprise_id = args.enterprise_id
        if enterprise_id is None:
            ent = await client.get_enterprise(GetEnterprise())
            enterprise_id = ent.id_
            logger.info(f"Auto-discovered enterprise: {ent.name} (ID {enterprise_id})")

        # Discover edges.
        logger.info(f"Fetching edges for enterprise {enterprise_id}...")
        edges = await client.get_enterprise_edges(
            GetEnterpriseEdges(enterprise_id=enterprise_id)
        )
        logger.info(f"Found {len(edges)} edges total")

        # Filter to connected edges.
        connected = [e for e in edges if e.edge_state == EdgeState.CONNECTED]
        if args.edge_name:
            connected = [
                e
                for e in connected
                if isinstance(e.name, str) and args.edge_name.lower() in e.name.lower()
            ]

        if not connected:
            logger.warning("No connected edges found matching criteria")
            return

        logger.info(
            f"{len(connected)} connected edge(s) to diagnose: "
            + ", ".join(
                e.name if isinstance(e.name, str) and e.name else "?" for e in connected
            )
        )

        # Run ARP_DUMP via WebSocket with JSON output.
        semaphore = asyncio.Semaphore(args.concurrency)
        results: list[EdgeArpResult] = []

        async def run_one(
            ws: VcoWebSocket, edge: GetEnterpriseEdgesItem
        ) -> EdgeArpResult:
            async with semaphore:
                name = edge.name
                if not isinstance(name, str) or not name:
                    name = f"edge-{edge.id_}"
                lid = edge.logical_id
                if not isinstance(lid, UUID):
                    return EdgeArpResult(edge_name=name, error="no logical_id")

                logger.info(f"Running ARP_DUMP on {name}...")
                try:
                    data = await ws.run_diagnostic(
                        lid,
                        "ARP_DUMP",
                        {"count": "100"},
                        resformat="JSON",
                        timeout=args.timeout,
                    )
                    entries = parse_arp_json(data)
                    logger.info(f"  {name}: {len(entries)} ARP entries")
                    return EdgeArpResult(edge_name=name, entries=entries)
                except VcoWebSocketError as exc:
                    logger.warning(
                        "ARP_DUMP failed for %s (%s)", name, type(exc).__name__
                    )
                    return EdgeArpResult(
                        edge_name=name, error=f"Request failed ({type(exc).__name__})"
                    )

        async with client.websocket() as ws:
            tasks = [run_one(ws, edge) for edge in connected]
            results = list(await asyncio.gather(*tasks))

    render_results(results, vco_host)


if __name__ == "__main__":
    asyncio.run(main())
