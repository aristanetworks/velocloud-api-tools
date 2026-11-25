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

## The patch format

`apply` takes a module, a patch and, optionally, an address. The address is
shorthand for nesting: `segment="Global Segment", path="outbound", patch=P` is
`patch={"segments": {"Global Segment": {"outbound": P}}}`. `path` is field
names joined by dots, each a field that holds one sub-model, except the last,
which may be any field; it does not step into list items, which the patch
names. `segment` names a segment like any other selector of the `segments`
list: by name, in any case, or by `segmentId`. Without an address, `patch`
must be a mapping.

A patch is strict and all or nothing:

| Value | Field | Effect |
|---|---|---|
| object | holds a sub-model | merged into it; built from the patch when unset |
| object or list | a list | item operations, or a list value that replaces the list (below) |
| object | a map of models (`dict[K, Model]`) | item operations by map key |
| `null` | type includes `MISSING` | unset |
| `null` | type includes `None`, not `MISSING` | `None` |
| `null` | neither | `PatchError` |
| anything else | any | replaces the value |

- Every value is validated as the field is when a response is parsed. Fields
  that tolerate a bad value from the VCO (turning it into the default) do not
  in a patch: it raises.
- A patch runs the field validators of what it assigns. The model-level
  validators (`@model_validator`) of a model that already exists do not run
  when a patch changes it; an item or sub-model built whole from a patch is
  validated whole. So a patch that deletes every netflow collector while
  `enabled` stays true is accepted, though parsing that state would refuse it.
- A key that names no field raises, at every depth, and so do two keys that
  name one field (`rule_id` and `ruleId`). Keys that start with `_` are
  directives: `_op` and `_position`, on a list item only.
- A patch that cannot be applied raises `PatchError` (a `ValueError`), whose
  `path` is where in the patch it failed. Resolver errors keep their types
  (`ResolutionError` for an unknown name). Either way the state, and
  `plan()`, are as they were.

Every list has a kind, which says how its items are named:

| Kind | Declared by | Items are named by |
|---|---|---|
| keyed | `KeyField(...)` | the key (for a composite key, a tuple of its parts or its string form), alt key, label or position |
| value | being `list[str]` or `list[int]`, without `Positional()` | the value, or a position |
| positional | `Positional()`, or a list of models that declares nothing | a position |
| untyped | being `list[Any]` or `list[dict]` | not at all: give a list value |
| map | being `dict[K, Model]` | the map key; no order, so no positions or moves |

An object on a list is item operations, keyed by selector. A selector is a
key, alt key, label or value in string form (`"10"` names VLAN 10),
`at(n)` (the item at position `n` of the list as it was), or `"+"` for a new
item. Under it, a plain object changes the item (and may rename it to a key no
other item has), `add(body, ...)` adds one, `delete()` removes it, and
`move(...)` moves it. A place is one of `first=True`, `last=True`,
`before=selector` or `after=selector`; an `add` without one goes last. A
selector that names no item, or more than one, raises and lists the matches;
`add` of a key or value already in the list raises. Every selector is resolved
against the list as it was, then changes, deletes, and adds and moves (in the
order written) are applied.

```python
from pyvelo_intent import add, at, delete, move

# keyed: firewall rules
await pipeline.apply(
    state,
    module="firewall",
    segment="Global Segment",
    path="outbound",
    patch={
        "block-ssh": add(
            {"action": {"allow_or_deny": "deny"}, "match": {"dport_low": 22}},
            before="allow-web",
        ),
        "allow-web": {"match": {"dport_high": 8443}},
        "old-rule": delete(),
        "guest-wifi": move(first=True),
    },
)
# value: SNMP allowed IPs
await pipeline.apply(
    state,
    module="device_settings",
    path="snmp.v2c.allowed_ips",
    patch={"10.0.0.1": add(), "10.0.0.9": delete()},
)
# positional: the rules of one BGP filter
await pipeline.apply(
    state,
    module="device_settings",
    segment="Global Segment",
    path="bgp.filters",
    patch={
        "from-isp": {
            "rules": {
                at(0): {"action": {"type": "PERMIT"}},
                at(2): delete(),
                "+": add(
                    {"match": {"value": "10.9.0.0/16"}, "action": {"type": "DENY"}},
                    after=at(0),
                ),
            }
        }
    },
)
```

`add`, `delete`, `move` and `at` return plain JSON values, so a patch is JSON
whether or not they are used. `VcoModel.merge_patch` and `ConfigPipeline.apply`
document the full rules in their docstrings.

The `segments` lists have rules of their own:

- On an edge, a segment the edge inherits from its profile counts as there. A
  change or a move of one the edge layer lacks first adds its placeholder
  entry; `add` of an inherited segment raises (write a plain object), and so
  does `delete` of one the edge layer has no entry for.
- On a profile, `apply` refuses `add` and `delete` of a segment: a segment
  belongs to all three of device settings, QoS and firewall, and adding one
  through a patch is not supported yet. Changing or moving one is fine.

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
  suggestions. A segment selector such as `guest` names the segment `Guest`,
  for every operation (which of them a `segments` list takes is in "The patch
  format"). A segment the edge lacks takes its descriptor from the profile; a
  name that neither the profile nor a loaded resolver has raises instead of
  adding a segment with another one's IDs.
- Renaming an item that was read with an alt key (a firewall or QoS rule's
  logical ID, a segment's `segmentId`) is one change of its name: keys the
  models do not know on that item are kept. A change of the key in a list
  without an alt key is the old item removed and a new one added, and those
  keys are not kept. A WAN link's name is a label, not its key, so renaming a
  link read with its `internalId` is a plain change. An item read without its ID (for example a QoS rule
  the VCO returned with no `ruleLogicalId`) that is then renamed is also saved
  as a removal plus an addition.
- A patch that moves items (a `move`) shows in `plan()` as one change at the
  list; in a keyed list the items' unknown keys are still kept.
- A cache of loaded modules must keep the JSON rows or `module.raw_data`. A
  `model_validate(module.model_dump())` round trip loses the raw snapshot, and
  with it the "as read" guarantee above.

## Scope and data handling

- Updates support firewall, device settings, QoS, and WAN modules. A missing
  edge firewall or QoS module can be created; it starts empty and is inserted
  on save. Control plane helpers support inspection and in-memory patches,
  but the save path does not support writing control plane modules.
- A failed `apply` changes nothing. Patches and saves run one after another,
  though: a failure leaves earlier patches, and the remote writes of a save
  that stopped part way, applied; there is no transaction or rollback.
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
| `pyvelo_intent` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, the patch helpers (`add`, `delete`, `move`, `at`) and `PatchError`, diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), error types, helpers (`build_default_pipeline`, `confirm_save`, `load_config`) |
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
