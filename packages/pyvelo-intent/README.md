# pyvelo-intent

Configuration intent layer for VeloCloud, built on top of
[`pyvelo`](../pyvelo/). Provides the `ConfigPipeline`
(load → snapshot → apply → diff → save), an aspect registry for focused
configuration projections, a generic intent diff engine, validators, and
helpers for scripting against the VCO configuration API.

Requires Python **3.15 or newer** and `pyvelo` **0.2.0 or newer**.
Licensed under Apache-2.0.

## Install

From the workspace root:

```bash
uv sync --all-packages
```

As Git dependencies from the public repository:

```toml
[project]
dependencies = ["pyvelo", "pyvelo-intent"]

[tool.uv.sources]
pyvelo = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo" }
pyvelo-intent = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo-intent" }
```

For reproducible deployments, set both Git sources to the same reviewed commit
using `rev = "<commit-sha>"`. This installation route does not require a PyPI release.

## Preview a configuration change

Set `VCO_HOST` to your Orchestrator hostname and `VCO_TOKEN` to an API token
with the appropriate enterprise permissions. This helper uses `VCO_TOKEN`;
the separate read-only examples use `VCO_API_TOKEN`.

```python
import asyncio

from pyvelo.client.vco_client import VcoClient
from pyvelo.config.validation import ValidatorRegistry
from pyvelo_intent import ConfigPipeline, get_vco_credentials
from pyvelo_intent.config.validators import key_uniqueness, required_fields


async def main() -> None:
    host, token = get_vco_credentials()
    validators = ValidatorRegistry()
    validators.register(key_uniqueness)
    validators.register(required_fields)

    async with VcoClient(host, token) as client:
        pipeline = ConfigPipeline(client, validator_registry=validators)
        # Replace these synthetic IDs with the intended enterprise and edge.
        state = await pipeline.load(101, enterprise_id=1)
        state = await pipeline.apply(
            state, module="firewall", patch={"firewall_enabled": True}
        )
        preview = await pipeline.save(state, dry_run=True)
        preview.raise_for_errors()
        print("Modules that would change:", preview.modules_updated)


asyncio.run(main())
```

`dry_run=True` skips write API calls; loading still reads live configuration.
Calling `save(state)` sends configuration writes. Check `SaveResult` with
`raise_for_errors()` to surface validation failures. `build_default_pipeline`
registers edge/profile name resolvers and aspects; loading also initializes the
segment resolver. Validators must be registered explicitly.

Application, address-group, and service-group name resolution require additional
resolvers. For application names, register
`ApplicationResolver(client)` from `pyvelo.config.resolvers.application` through
`pipeline.resolvers.register(...)` before loading; the pipeline then loads it
automatically. For other resolver types, register and load them explicitly with
`state.resolver_context`, or supply numeric IDs in patches.

## Scope and data handling

- Updates support firewall, device settings, QoS, and WAN modules. Creating
  a missing edge module from its profile supports firewall and QoS. Control
  plane helpers and aspects support inspection and in-memory patches, but
  the save path does not support writing control plane modules.
- Batch aspect patches and saves run sequentially. A failure can leave earlier
  in-memory patches or remote writes applied; there is no transaction or rollback.
  There is no concurrent-edit conflict detection. Reload configuration after a
  successful save before starting another change; save does not refresh the snapshot.
- The `override_consistency` validator is a traversal placeholder and currently
  reports no violations. Other registered validators cover specific structural
  or domain checks; they do not guarantee that every patch is valid for a live VCO.
- `ConfigState`, field diffs, aspect reads, and validation messages can contain
  complete configuration values, including addresses and configuration secrets.
  Redact them before logging, sharing, or committing exports. This library does
  not persist configuration automatically or redact returned values.
- Keep API tokens and environment files outside version control. TLS verification
  uses the SDK default. Tests use synthetic fixtures and do not require a live VCO.

## Surface

| Module | What it provides |
|---|---|
| `pyvelo_intent` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, `AspectRegistry`, `Aspect`, diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), error types, helpers (`build_default_pipeline`, `confirm_save`, `get_vco_credentials`, `load_config`, `generate_patch_schema`) |
| `pyvelo_intent.config.pipeline` | `ConfigPipeline` (load/load_profile → apply/apply_aspect → save), `build_default_pipeline` |
| `pyvelo_intent.config.state` | `ConfigState`, `SaveResult`, `ConfigFieldChange`, `ValidationWarning` |
| `pyvelo_intent.config.aspect` | `Aspect`, `AspectSummary`, `AspectRegistry` (register, list, search, get, patch/read schema generation) |
| `pyvelo_intent.config.aspect_query` | Navigation helpers: `navigate_to_base`, `get_aspect_data`, `get_aspect_parent` |
| `pyvelo_intent.config.aspects.*` | Predefined aspects for firewall, device settings, QoS, WAN, and control plane modules |
| `pyvelo_intent.config.schema` | `generate_patch_schema`, `resolve_model_at_path` — JSON Schema generation with patch transforms |
| `pyvelo_intent.config.helpers` | `get_vco_credentials`, `load_config`, `confirm_save` |
| `pyvelo_intent.config.validators.*` | Structural built-ins (`key_uniqueness`, `override_consistency`, `required_fields`) + domain validators (firewall, device settings) |
| `pyvelo_intent.config.(firewall\|device_settings\|qos\|wan\|control_plane)` | Per-module configuration helpers |
| `pyvelo_intent.diff` | Generic keyed-list diff engine: `diff_intents`, `DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange` |
| `pyvelo_intent.errors` | `IntentError`, `ConfigInputError`, `MissingEnvVarError`, `SaveValidationError`, `UnknownRegistryEntryError` |
| `pyvelo_intent.testing` | Test fixtures: `MockVcoClient`, `make_firewall_module`, `make_firewall_segment` |

## Tests

```bash
uv run --package pyvelo-intent pytest packages/pyvelo-intent/tests
uv run basedpyright packages/pyvelo-intent
uv run ruff check packages/pyvelo-intent
```
