# pyvelo

Python SDK for the VeloCloud Orchestrator (VCO) API. Pydantic-typed
client, request/response schemas, enums, models, and resolver/validation
framework.

## Install

From the workspace root:

```bash
uv sync
```

As a git dependency:

```toml
[project]
dependencies = ["pyvelo"]

[tool.uv.sources]
pyvelo = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo" }
```

## Surface

| Module | What it provides |
|---|---|
| `pyvelo.client` | `VcoClient` (async JSON-RPC), `VcoClientProtocol`, `VcoAuth`, `get_vco_credentials` (reads `VCO_HOST` / `VCO_TOKEN`, optionally from a `.env` file with the `dotenv` extra), `VcoWebSocket` / `PendingDiagnostic`, `ExchangeHook`, request/response schemas, enums, operations, VCO data models |
| `pyvelo.core` | `VcoModel` base class (with `.merge_patch()`), field markers (`KeyField`, `Resolvable`, `NameOf`, `OverrideFlag`, `Alias`, `Normalizer`, `ConfigContext`, `ContextDefault`), defaulting helpers (`DefaultingModel`, `DefaultingScalar`), `Secret` (marks a field that holds a secret), `OwnsRef` and `RefOnly` (mark the data field a ref row goes with, and a field that stands for a ref row and is never dumped or read from data), `get_owns_ref` and `get_ref_only`, `is_secret` and `hide_secrets(model, path, value)` (the value with its secrets shown as `***`), identity types (`EdgeId`, `SegmentId`, `ProfileId`, …); the patch helpers (`add`, `delete`, `move`, `at`), `PatchError` and `PatchErrorKind` (the type of its `kind`); for a tool that works over patches, `Path` and `Key` (the parts of a change's location), `key_text` and `item_key_text`, `list_kind` and `ListKind`, `select_items` and `item_selector`, `key_selector` and `selector_of`, `nest_patch`, `position_of`, `is_positional` and `get_name_of`; facts about a field for a schema view: `wire_name` (the JSON key it is written under), `python_name` (the field a key names; `python_path` in `pyvelo.core.rebase` does the same for the path of a change), `field_models` (the model classes its value, its list items or its map values can be), and `Model.context(level)`, whose entries say whether a field exists at edge or at profile level (`is_present`) |
| `pyvelo.client.module_write`, `pyvelo.client.module_check` | `plan_module` and `ModulePlan` (what a write of a module changes), `check_module_write` and `ModuleWrite` (a write that was stored: the module as stored, the changes `applied`, `left_out` and `server_changes`) |
| `pyvelo.config.resolver` | `Resolver` Protocol, `BaseResolver`, `ResolverRegistry`, `ResolverContext`, `ResolutionError` |
| `pyvelo.config.resolvers.*` | Concrete resolvers: edge, segment, profile, address_group, application, service_group, license |
| `pyvelo.config.validation` | `Validator` Protocol, `ValidatorRegistry`, `@validator` decorator, `ConfigStateLike` Protocol, `ValidationError`, `ValidationResult` |
| `pyvelo.testing` | Test fixtures: `FakeEdge`, `FakeSegment`, `mock_vco_client_factory`, `make_jsonrpc_response`, `make_transport`; `ModuleStore`, a fake VCO that holds configuration module rows and serves their reads and writes (`store_of`, `as_stored`) |

`VcoClient(host, token)` strips the whitespace around the token. A token that
is empty, or has a line break, a tab, another control character or a non-ASCII
character in it, raises `VcoCredentialsError` when the client is built; the
message does not show the token.

## Writing a configuration module

`VcoClient.update_module(module, data)` writes a module's data as it was read,
with only the caller's changes applied, and returns the module as stored, of
the class of `module`. When nothing changed it sends nothing and returns
`module` itself. It is `plan_module` (`pyvelo.client.module_write`) followed by
`write_module`.

The module you pass in is not updated: it keeps the data and the `version` from
before the write. Continue from the module `update_module` returns (`module =
await client.update_module(module, data)`), and after `ConfigConflictError` or
`WriteNotAppliedError` from `error.module`. After `WriteOutcomeUnknownError` the
module as stored could not be read and the write may be stored: read the
configuration again and compare before you write this module again.
`update_module` marks the module you pass in as replaced when the write stored
something, and after `ConfigConflictError` or `WriteNotAppliedError`; it does
not mark it when nothing changed, after `WriteOutcomeUnknownError`, or after an
error before the VCO answered. Calling `update_module` again with a marked
module raises `DetachedObjectError` before anything is sent, and so does an
assignment through its `data`. `write_module` does not mark the module it is
given. `update_module` returns only the module; to see what the VCO stored its
own way, use `write_module`, whose `ModuleWrite` has `left_out` and
`server_changes`.

`VcoClient.write_module(module, plan)` sends a plan as an update or an insert
that asks for the module as stored (`returnData`), and reads the module back
when the VCO answers without it. An update none of whose changes could be found
stored (every one is to a value the VCO stores its own way) is sent without
`returnData`. The VCO then answers with a row count, which says whether it stored the write, and the module is read
back. `write_module` then compares what the VCO stored with what the plan
meant to change. When every planned change is stored it returns a `ModuleWrite`:
the module as stored, the planned changes `applied`, and the `server_changes`
(what else the VCO stores otherwise than sent, such as an ID it issued).
Otherwise it raises, from `pyvelo.client.exceptions`:

- `ConfigConflictError` when the VCO refused the version and the module as
  stored does not already hold every planned change, or when it answered with
  the module, stored none of the changes and holds each value as read
  (`error.stated` is then False: the refusal is inferred);
- `WriteNotAppliedError` when the VCO stored the write otherwise than as sent.
  That includes a stale write to a VCO whose conditional version check is off,
  when another writer changed a value the plan also changes: that edit is
  marked as a clash;
- `WriteOutcomeUnknownError` when the write was sent and the module as stored
  could not be read: the read-back did not find it or it does not parse, or the
  VCO's answer is not this module's row. It has no lost edits; `error.answer`
  says whether the VCO refused the write, counted it, or answered with a row.
  When the read itself fails, its own error is raised, and the write may be
  stored.
  `write_module(..., if_absent=True)` raises it too, before it sends anything,
  when the read it makes before an insert whose earlier answer was lost cannot
  be used: an earlier insert of the module may be stored.

The first two carry `lost_edits` (the planned changes not stored, with the
values as read, meant and stored), `applied`, `left_out`, `server_changes`, the
`plan` and the `module` as stored. `error.redo_patch()` is the patch that redoes
the lost edits on that module (`error.module.data.merge_patch(error.redo_patch())`);
it leaves out a clash unless asked, and `error.not_redone()` lists what it
leaves out. On a `WriteOutcomeUnknownError` the patch is empty: nothing is
known to be lost. Where the VCO stores a value its own way (a field marked
`ServerOwned`, and the rewrites per module in `MODULE_REWRITES`,
`pyvelo.client.module_check`), a planned change is not a lost edit: it is in
`left_out` of the write, or of one of the first two errors, and a value other
than the one read is a server change. After a refusal, and when the VCO answered
with a module that holds none of the changes, such a change that is still as
read is a lost edit. `check_module_write(module, plan, stored, answer=...)` takes
what the VCO said of the write: `"row"`, `"counted"` or `"refused"`. With `"row"`
it has only the module to go by, so a write none of whose changes is found stored
is taken as not stored.

With `if_absent=True` an insert first reads the configuration, and a module of
that name found there is taken as the insert's answer and checked like one, with
nothing inserted: the way to repeat an insert whose answer never arrived.

`pyvelo.testing.ModuleStore` is an `httpx` handler that holds module rows as
JSON and answers module updates, inserts, `getConfiguration` and, for an edge
whose two configurations it was told (`add_stack`), `getEdgeConfigurationStack`,
as a VCO does. Its knobs are the conditional version check, a VCO that ignores
`returnData`, the enterprise a request has to name, writes that fail, writes
that are stored and whose answer is lost (`lose_answers`), rule IDs issued as a
VCO issues them (`issue_rule_ids`), hooks that change what is stored
(`on_store`) and another writer's change (`other_writer`). A client over it is
`VcoClient(host, token, transport=make_transport(store))`: it runs its own
request building, parsing and error mapping against the store. A client given a
`transport` is connected from the start and needs no `async with`; inside one it
keeps the transport, and after it the client is closed.

### Device settings fields that stand for ref rows

A ref row sits beside a module's `data`, keyed by ref name, and associates the
module (usually one of its segments) with an enterprise object. Some device
settings fields go with such a row. `DeviceSegment.segment` (profile level) and
`CssProviderRef.logical_id` hold an identity the data already has; the row
`deviceSettings:segment` or `deviceSettings:css:provider` goes with it.
`SegmentAuthentication.service`, `DnsPrimaryProvider.service` and
`DnsBackupProvider.service` hold a value only the row says: the data has a stub
(`{"ref": "deviceSettings:dns:primaryProvider"}`) and nothing that names the
provider. That `service` is the network service of the row of the stub's ref
name for the segment the provider sits in (`deviceSettings:authentication`,
`deviceSettings:dns:primaryProvider` or `deviceSettings:dns:backupProvider` when
the stub has none). It is never part of `data` on the wire, and a `service` key
in the data is not read. A provider given as a list owns no row.

A module is filled when it is validated with both its data and its refs: `None`
means the segment has no row. A module from `getConfigurationModules` or from an
insert's `returnData` was not read with its refs (`module.refs_loaded` is
`False`), and its `service` fields are `MISSING`; a module from
`getConfiguration`, the edge stack or an update's `returnData` is loaded, and an
absent `refs` key means no rows. `ref_owners(data, level)` in
`pyvelo.client.models.configuration.ref_owners` lists the fields that hold an
identity, with their ref name, segment and path.

The refs of a write are derived from these fields, never given: `plan_module`
and `update_module` take no `refs`. An edit that sets or changes one of them
adds the row it goes with, and one that clears it removes the row; every other
row is sent as read, and `plan.ref_changes` lists the rows added and removed
(`plan.render()` shows them as `+ refs <name> (<segment>): <value>` and `- refs
...`). A new row needs the object's ID, which the `resolvers=` argument finds
(`ResolverNotLoadedError` when it is missing). A device settings or QoS update
with segments always carries its refs, as read when nothing in them changed,
because the VCO runs its processors on every such write. A module read without
its refs (`refs_loaded` is `False`) cannot be written that way, nor with an edit
to one of these fields: `RefsNotLoadedError` says to read it with
`configuration/getConfiguration` and `modules`, or from the edge stack. The ref
names that are not derived are written with `update_configuration_module`, the
low-level request.

## Portal list and pagination methods

| Method | Use for | Source requests |
|---|---|---|
| `portal_list` | Non-paginated list endpoints | One. A complete paginated envelope is unwrapped; an incomplete one raises. |
| `portal_paginated` | Callers that control paging | One per yielded page. |
| `portal_paginated_list` | Paginated endpoints returning a list | Until the last page, or `request._max_pages` pages when it is non-zero. |

Both paginated methods record `_page_has_more` and `_page_next_link` on the
caller's request after every page; the caller's `next_page_link` is not
modified. After a capped `portal_paginated_list`, `_page_has_more=True` marks
the result incomplete and `_page_next_link` resumes from the next page. A page
reporting `more` without a cursor raises instead of repeating the request.

## Source request observations

Pass an optional synchronous `request_observer` callback to `VcoClient` to
receive a frozen `RequestObservation` for each completed transport call,
including errors and cancellation. Observations count HTTP dispatch attempts,
429 responses, limiter/retry waits, numeric Retry-After delays, source latency,
and the client's configured rate limit and retry maximum. Each pagination call
produces its own observation. The existing limiter and retry policy are unchanged.

Observations contain the endpoint path, RPC method and cursor presence, with no
host, query string, headers, credentials, cursor value or response payload. An
`http_response` outcome means HTTP completed; it does not establish RPC success,
source completeness or metric availability. The observer must be lightweight;
callback errors are logged without their contents and preserve the source result.

## Tests

```bash
uv run --package pyvelo pytest packages/pyvelo/tests
```
