# pyvelo-intent

Configuration intent layer for VeloCloud, built on top of
[`pyvelo`](../pyvelo/). Provides the `ConfigPipeline`
(load → snapshot → apply → diff → save), a generic intent diff engine,
validators, and helpers for scripting against the VCO configuration API.

Requires Python **3.13 or newer** and `pyvelo` **0.2.0 or newer**.
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
with the appropriate enterprise permissions. 

```python
import asyncio

from pyvelo.client import VcoClient, get_vco_credentials
from pyvelo.config.validation import ValidatorRegistry
from pyvelo_intent import ConfigPipeline
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
registers edge/profile name resolvers; loading also initializes the
segment resolver. Validators must be registered explicitly.

Application, address-group, and service-group name resolution require additional
resolvers. For application names, register
`ApplicationResolver(client)` from `pyvelo.config.resolvers.application` through
`pipeline.resolvers.register(...)` before loading; the pipeline then loads it
automatically. For other resolver types, register and load them explicitly with
`state.resolver_context`, or supply numeric IDs in patches.

## What a save writes

A save sends each changed module's `data` as it was read, with only your
changes applied. Keys the models do not know are preserved, and a module you
did not change is not sent.

```text
firewall: update
  + firewall_enabled: false
  reconciled segments: Guest
```

- The module `version` from the read is sent. If the module changed on the VCO
  since, the save raises `ConfigConflictError` (from `pyvelo.client.exceptions`).
- A `ConfigState` can write once. After a save that sent anything, load again;
  that is also how server-assigned rule IDs are obtained. Applying to or saving
  a spent state raises `StateSpentError`.
- `state.plan().render()` shows each module's changes against the raw data as
  read, as in the example above. `SaveResult.plan` is the same plan, and
  `dry_run=True` reports it without writing.
- When the profile has segments the edge module lacks, a write to that module
  adds them as entries that inherit everything. The plan lists them under
  "reconciled segments".
- Deleting an edge segment that the profile also has leaves an empty entry for
  it in the write, as for a segment the edge never had.
- A missing edge firewall or QoS module starts empty; nothing is copied from
  the profile.
- A firewall segment has no `outboundOverride` key in the VCO or on the edge. If
  an older version of this library wrote one into a module, writing that module
  drops it from every segment entry and the plan lists each removal (`-
  segments[...].outboundOverride`). A module with nothing else to write stays
  `none`: the plan reports the key under "legacy keys" and the save leaves it.
- Setting a field to its default where the edge inherits it raises
  `LossyFieldError` until that section is converted (planned), because the
  written data cannot express it. So does an edit to a section the edge layer
  does not write at all. `LossyFieldError` is importable from
  `pyvelo.client.exceptions` (also from `pyvelo.client` and `pyvelo`), and
  `get_changed_modules()` and `plan()` raise it as well as `save()`.
- Names resolve on an exact or case-insensitive match; a near miss raises with
  suggestions. A segment key such as `guest` edits or adds the segment `Guest`.
  A segment the edge lacks takes its descriptor from the profile; a name that
  neither the profile nor a loaded resolver has raises instead of adding a
  segment with another one's IDs.
- Renaming a keyed item (a rule, a segment) rewrites it whole: keys the models
  do not know on that item are not kept.
- A cache of loaded modules must keep the JSON rows or `module.raw_data`. A
  `model_validate(module.model_dump())` round trip loses the raw snapshot, and
  with it the "as read" guarantee above.

## Scope and data handling

- Updates support firewall, device settings, QoS, and WAN modules. A missing
  edge firewall or QoS module can be created; it starts empty and is inserted
  on save. Control plane helpers support inspection and in-memory patches, but the save path does not support writing control plane modules.
- Multiple patches and saves run sequentially. A failure can leave earlier
  in-memory patches or remote writes applied; there is no transaction or rollback.
  Reload configuration after a successful save before starting another change;
  save does not refresh the snapshot.
- The `override_consistency` validator is a traversal placeholder and currently
  reports no violations. Other registered validators cover specific structural
  or domain checks; they do not guarantee that every patch is valid for a live VCO.
- `ConfigState`, field diffs, and validation messages can contain
  complete configuration values, including addresses and configuration secrets.
  Redact them before logging, sharing, or committing exports. This library does
  not persist configuration automatically or redact returned values.
- Keep API tokens and environment files outside version control. TLS verification
  uses the SDK default. Tests use synthetic fixtures and do not require a live VCO.

## Surface

| Module | What it provides |
|---|---|
| `pyvelo_intent` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), error types, helpers (`build_default_pipeline`, `confirm_save`, `load_config`) |
| `pyvelo_intent.config.pipeline` | `ConfigPipeline` (load/load_profile → apply → save), `build_default_pipeline` |
| `pyvelo_intent.config.state` | `ConfigState`, `SaveResult`, `ConfigFieldChange`, `ValidationWarning` |
| `pyvelo_intent.config.helpers` | `load_config`, `confirm_save` |
| `pyvelo_intent.config.validators.*` | Structural built-ins (`key_uniqueness`, `override_consistency`, `required_fields`) + domain validators (firewall, device settings) |
| `pyvelo_intent.config.(firewall\|device_settings\|qos\|wan\|control_plane)` | Per-module configuration helpers |
| `pyvelo_intent.diff` | Generic keyed-list diff engine: `diff_intents`, `DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange` |
| `pyvelo_intent.errors` | `IntentError`, `ConfigInputError`, `SaveValidationError`, `StateSpentError`, `UnknownRegistryEntryError` |
| `pyvelo_intent.testing` | Test fixtures: `MockVcoClient`, `make_firewall_module`, `make_firewall_segment` |

## Tests

```bash
uv run --package pyvelo-intent pytest packages/pyvelo-intent/tests
uv run basedpyright packages/pyvelo-intent
uv run ruff check packages/pyvelo-intent
```
