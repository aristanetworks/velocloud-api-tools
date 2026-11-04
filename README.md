# pyvelo

Python SDK for the VeloCloud Orchestrator (VCO) API, a configuration intent library, and a small set of read-only examples.

Requires Python **3.15 or newer**.

## Development

    uv sync
    uv run --package pyvelo pytest packages/pyvelo/tests
    uv run --package pyvelo-intent pytest packages/pyvelo-intent/tests
    uv run basedpyright
    uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors --help

For SDK usage, see [packages/pyvelo/README.md](packages/pyvelo/README.md). For configuration loading, patching, validation, and saving, see [packages/pyvelo-intent/README.md](packages/pyvelo-intent/README.md). For the included examples and their VCO data handling, see [packages/pyvelo-examples/README.md](packages/pyvelo-examples/README.md).
