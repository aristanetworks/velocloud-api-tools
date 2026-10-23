# pyvelo-examples

Five read-only examples for the `pyvelo` SDK. They query enterprise analytics,
inventory, configuration, and diagnostic data from a VeloCloud Orchestrator
(VCO). They do not write configuration to the VCO.

## Install and configure

From the repository root, install the examples and their `pyvelo` dependency:

```bash
uv sync --package pyvelo-examples
```

Set credentials in the environment or in a local `.env` file. The
`.credentials/` directory is ignored by Git:

```bash
mkdir -p .credentials
cat > .credentials/lab.env <<'EOF'
VCO_HOST=vco.example.com
VCO_TOKEN=replace-with-your-token
EOF
```

Pass `--env .credentials/lab.env` to scripts that accept `--env`, or export
`VCO_HOST` and `VCO_TOKEN` in the process environment. Environment variables
already set take precedence over values from the file.

## Examples

Run modules from the repository root with `uv run --package pyvelo-examples
python -m pyvelo_examples.<module>`.

| Module | Purpose |
|---|---|
| `analytics_overview` | Print flow, health, link, and firewall analytics for connected edges. |
| `analytics_report` | Write a local HTML report with analytics charts. |
| `analytics_smoke_test` | Call analytics endpoints and print a pass/fail summary. |
| `get_bgp_neighbors` | Print BGP neighbors as a table, CSV, or JSON. |
| `run_arp_dump` | Run the read-only ARP diagnostic on connected edges. |

For example:

```bash
uv run --package pyvelo-examples python -m pyvelo_examples.analytics_overview \
  --env .credentials/lab.env --enterprise-id 123
uv run --package pyvelo-examples python -m pyvelo_examples.get_bgp_neighbors \
  --env .credentials/lab.env --enterprise-id 123 --format csv
uv run --package pyvelo-examples python -m pyvelo_examples.analytics_report \
  --env .credentials/lab.env --hours 24
```

The enterprise ID `123` above is illustrative; provide an ID from your own
VCO. Scripts that support automatic discovery can omit it.

## Handling VCO data

Reports, terminal output, and optional exchange dumps contain live topology,
configuration, or analytics data. Treat them as sensitive and do not commit or
share them publicly. Git ignores the default `analytics_report.html` report path and the `vco_dump/`
directory. The `analytics_smoke_test --dump-dir` option is opt-in and writes raw
request and response bodies to the selected directory. If you use a different
path, keep those sensitive files out of version control.
