# pyvelo

Python SDK for the VeloCloud Orchestrator (VCO) API, with a small set of read-only examples.

Requires Python **3.14 or newer**.

## Development

    uv sync
    uv run --package pyvelo pytest packages/pyvelo/tests
    uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors --help

For SDK usage, see [packages/pyvelo/README.md](packages/pyvelo/README.md). For the included examples and their VCO data handling, see [packages/pyvelo-examples/README.md](packages/pyvelo-examples/README.md).
