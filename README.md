<div align="center">

# pyvelo

**A typed, async Python toolkit for the VeloCloud Orchestrator (VCO) API**

[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![uv](https://img.shields.io/badge/built%20with-uv-DE5FE9)](https://docs.astral.sh/uv/)

</div>

| Package | What it does |
|---|---|
| [**`pyvelo`**](packages/pyvelo/) | Async JSON-RPC client with Pydantic models for every request, response, and configuration module. It handles pagination, rate limiting, and retries. Analytics come back as Polars frames. |
| [**`pyvelo-intent`**](packages/pyvelo-intent/) | A load → patch → diff → save pipeline for edge and profile configuration, with addressed, strict patches and validators. |
| [**`pyvelo-examples`**](packages/pyvelo-examples/) | Read-only CLI scripts for BGP neighbors, ARP dumps, and analytics reports. |

## Install

Add the packages to your project as Git dependencies:

```toml
[project]
dependencies = ["pyvelo[analytics,dotenv]", "pyvelo-intent"]

[tool.uv.sources]
pyvelo = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo" }
pyvelo-intent = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo-intent" }
```

## pyvelo: talk to the API

**Find edges that aren't connected**

```python
import asyncio

from pyvelo.client import VcoClient, get_vco_credentials
from pyvelo.client.enums import EdgeState
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges


async def main() -> None:
    host, token = get_vco_credentials()
    async with VcoClient(host, token) as client:
        edges = await client.get_enterprise_edges(GetEnterpriseEdges(enterprise_id=1))
        for edge in edges:
            if edge.edge_state != EdgeState.CONNECTED:
                print(f"{edge.name}: {edge.edge_state}")


asyncio.run(main())
```

**Top applications on an edge over the last 24 hours** (requires `pyvelo[analytics]`)

```python
import asyncio
from datetime import UTC, datetime, timedelta

from pyvelo.analytics import Analytics, FlowGroupBy, FlowMetric
from pyvelo.client import VcoClient, get_vco_credentials
from pyvelo.core import ApiInterval


async def main() -> None:
    host, token = get_vco_credentials()
    async with VcoClient(host, token) as client:
        analytics = Analytics(client, enterprise_id=1)
        frame = await analytics.get_edge_flow_metrics(
            42,
            ApiInterval(start=datetime.now(UTC) - timedelta(hours=24)),
            FlowGroupBy.APP,
            limit=5,
            sort=FlowMetric.TOTAL_BYTES,
        )
        print(frame.to_dataframe())  # a polars.DataFrame


asyncio.run(main())
```

## pyvelo-intent: change configuration safely

This example loads an edge by name, adds a firewall rule at the top of a segment's outbound rules, reviews the field-level diff, and previews the save:

```python
import asyncio

from pyvelo.client import VcoClient, get_vco_credentials
from pyvelo_intent import add, build_default_pipeline


async def main() -> None:
    host, token = get_vco_credentials()
    async with VcoClient(host, token) as client:
        pipeline = build_default_pipeline(client)
        state = await pipeline.load("branch-nyc", enterprise_id=1)

        # The address names the list and the patch names its items: add()
        # makes a new rule, a plain object changes one, delete() and move()
        # remove and reorder. A patch that fails raises PatchError and
        # changes nothing.
        state = await pipeline.apply(
            state,
            module="firewall",
            segment="Global Segment",
            path="outbound",
            patch={
                "block-ssh": add(
                    {
                        "action": {"allow_or_deny": "deny"},
                        "match": {"proto": 6, "dport_low": 22, "dport_high": 22},
                    },
                    first=True,
                )
            },
        )

        for change in state.get_field_changes():
            print(change.module, change.path)
        # firewall segments[Global Segment].outbound[block-ssh]

        result = await pipeline.save(state, dry_run=True)  # drop dry_run to write
        result.raise_for_errors()
        print("Would update:", result.modules_updated)


asyncio.run(main())
```

Without `dry_run`, the save asks the VCO for each module it writes, as stored, and the state continues from it: no reload is needed, the rule ID the VCO gives `block-ssh` is in the state, `state.plan()` is empty, and the state takes more edits and saves. A write the VCO did not store as planned raises `ConfigConflictError` or `WriteNotAppliedError`, which lists the lost edits to redo. A write whose stored module could not be read back raises `WriteOutcomeUnknownError`.

The [pyvelo-intent README](packages/pyvelo-intent/README.md) covers the patch format, what a save writes and reports, validators, resolvers, and the limits of the save path.

## Development

```bash
uv sync --all-packages
scripts/check.sh              # everything CI runs: lint, types, test
scripts/check.sh test         # or pick steps: lint | types | test
scripts/check.sh fix          # ruff check --fix + ruff format
```

Tests cover pyvelo and pyvelo-intent and don't need a live VCO. CI (`.github/workflows/ci.yml`) runs the same script on every pull request.

Licensed under [Apache-2.0](LICENSE).
