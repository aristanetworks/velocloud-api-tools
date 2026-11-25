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
when the VCO answers without it. An insert is always read back, whatever the
VCO answered, because an insert's `returnData` does not promise refs: the module
returned has its refs (`refs_loaded` is `True`) and is the one compared with the
plan. A QoS insert carries the plan's refs in its one request when the plan has
any; a firewall insert has none. An update none of whose changes could be found
stored (every one is to a value the VCO stores its own way) is sent without
`returnData`. The VCO then answers with a row count, which says whether it
stored the write, and the module is read back. `write_module` then compares
what the VCO stored with what the plan meant to change. When every planned
change is stored it returns a `ModuleWrite`: the module as stored, the planned
changes `applied`, and the `server_changes` (what else the VCO stores otherwise
than sent, such as an ID it issued). Otherwise it raises, from
`pyvelo.client.exceptions`:

- `ConfigConflictError` when the VCO refused the version and the module as
  stored does not already hold every planned change, or when it answered with
  the module, stored none of the changes and holds each value as read
  (`error.stated` is then False: the refusal is inferred). It is also raised
  before anything is sent (`error.sent` is False, `error.stated` False) when
  the write sends refs, the module's version on the VCO is not the one read, and
  the module there does not hold the changes. When the module is gone from its
  configuration, `error.module` is None and the message says to read the
  configuration again;
- `WriteNotAppliedError` when the VCO stored the write otherwise than as sent.
  That includes a stale write to a VCO whose conditional version check is off,
  when another writer changed a value the plan also changes: that edit is
  marked as a clash;
- `RefsDroppedError`, a `WriteNotAppliedError`, when every change not stored is
  a ref row: the VCO stored the data and skipped a row it could not resolve (an
  object it does not have), or kept a row the plan removed. Rows of refs are
  compared by identity, never by content. `error.lost_edits` names each row by
  its ref and identity, and `error.refs_as_read` holds the module's refs as
  read, less the rows of `deviceSettings:vnfs:edge`, `objectGroup:addressGroup`
  and `objectGroup:portGroup`, which the VCO keeps itself and would store twice
  if they were sent back. When the VCO took none of the rows sent
  (`error.every_row_lost`), put the rows back by sending
  `configuration/updateConfigurationModule` with
  `{"id": <module ID>, "_update": {"data": <the module's data as stored now>,
  "refs": <error.refs_as_read>}}` (`update_configuration_module`). A caller who
  speaks only JSON reads the data as stored now with
  `configuration/getConfiguration` and
  `{"id": <configuration ID>, "with": ["modules"]}`, and sends as `refs` the
  refs from the read the write was planned on, less those three names (the
  VCO's own refs now lack the rows it dropped). Otherwise, for a row you added, check that
  the object exists and set the field again on `error.module`; for a row you
  removed that the VCO kept, clear the field again. The message gives the patch
  for each. An update the VCO answered with the module needs one change that is
  stored beside: one whose only changes are ref rows, none of them stored, is a
  `ConfigConflictError`, and its message names both causes. A
  row sent as read that the returned refs lack, and any difference in the rows
  of the refs the VCO's own processors manage, are `server_changes`, not errors;
- `WriteOutcomeUnknownError` when the write was sent and the module as stored
  could not be read: the read-back did not find it or it does not parse, or the
  VCO's answer is not this module's row. It has no lost edits; `error.answer`
  says whether the VCO refused the write, counted it, or answered with a row.
  When the read itself fails, its own error is raised, and the write may be
  stored.
  `write_module(..., if_absent=True)` raises it too, before it sends anything,
  when the read it makes before an insert whose earlier answer was lost cannot
  be used: an earlier insert of the module may be stored.

The first three carry `lost_edits` (the planned changes not stored, with the
values as read, meant and stored), `applied`, `left_out`, `server_changes`, the
`plan` and the `module` as stored. `error.redo_patch()` is the patch that redoes
the lost edits on that module (`error.module.data.merge_patch(error.redo_patch())`);
it leaves out a clash unless asked, and `error.not_redone()` lists what it
leaves out. It redoes a lost provider row by setting the `RefOnly` field again
(`{"segments": {"Global Segment": {"dns": {"primaryProvider": {"service": "<logical ID>"}}}}}`,
`null` to clear it) and leaves out any other ref row; the segment is the one
the row was made for, found by its identity and named as the stored module names
it, and a row whose segment cannot be proved to be stored is left out. On a
`WriteOutcomeUnknownError` the patch is empty: nothing is known to be lost.
Where the VCO stores a value its own way (a field marked `ServerOwned`, and the
rewrites per module in `MODULE_REWRITES`,
`pyvelo.client.module_check`), a planned change is not a lost edit: it is in
`left_out` of the write, or of one of the first two errors, and a value other
than the one read is a server change. After a refusal, and when the VCO answered
with a module that holds none of the changes, such a change that is still as
read is a lost edit. `check_module_write(module, plan, stored, answer=...)` takes
what the VCO said of the write: `"row"`, `"counted"`, `"refused"` or `"stale"`.
With `"row"` it has only the module to go by, so a write none of whose changes
is found stored is taken as not stored. `"stale"` is for a write that was not
sent because the module's version had changed: it passes and fails as
`"refused"` does, and the `ConfigConflictError` says that nothing was sent.

A write that sends refs (an update whose plan carries them: a device settings or
QoS module with segments always does) costs one more request. With the VCO's
version precheck off, a stale write is refused for `data` but still replaces the
refs, so `write_module` first reads the module's `version`
(`configuration/getConfigurationModules` with `noData`, filtered to its name).
When it is the one read, the update is sent. When it is not, `write_module`
reads the configuration with its modules and checks the module there against the
plan: it returns that module as the write when it already holds the changes (a
retry whose first copy was stored), and otherwise raises `ConfigConflictError`
with nothing sent. The read narrows the race and does not close it, and a
module with no `version` is written unconditionally. A write that sends no refs
makes no extra request.

`write_module(..., rebase=True)` is for a module written after a profile's
device settings, which can change the profile's other modules (QoS rule flags,
and the versions of every module when a segment becomes in use). It reads the
`version` of every update, refs or none. When it changed, the module is read in
full and the plan is rebased onto it (`pyvelo.client.module_write.rebase_onto`):
your edits, the plan's changes, go onto the data as it is now, and the rebased
plan is sent with the version just read. The `ModuleWrite` holds that plan, and
its `server_changes` hold what changed beside your edits, each with `merged`
true. When the module holds every change already, nothing is sent and the write
is that module with operation `none`. A path the plan changes that also changed
there, and a list the plan edits an item of that had items added or removed,
raise `ConfigConflictError` with `error.sent` False and the module as it is now.
A plan that adds or removes a ref row cannot be rebased (`ValueError`). An
insert is not rebased.

With `if_absent=True` an insert first reads the configuration, and a module of
that name found there is taken as the insert's answer and checked like one, with
nothing inserted: the way to repeat an insert whose answer never arrived. That
read already holds the refs, so the module found is not read again.

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

A row for a new provider is added only for a service of the type its ref
names. `SERVICE_TYPES` in `pyvelo.client.derived_refs` gives it: `dns` for
`deviceSettings:dns:primaryProvider` and `deviceSettings:dns:backupProvider`,
`authentication` for `deviceSettings:authentication`, and `cloudSecurityService`
for `deviceSettings:css:provider` (tags of `EnterpriseServiceType`). A service
of another type raises `ResolutionError` that names the field, the service, its
type and the wanted type, and gives the patch that sets the field; a ref name
outside that table is not checked.

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

`module.refs` is read-only, because the write ignores it. `module.refs = ...`
raises a `ValidationError`, and the rows (`ConfigurationRef` and its
subclasses) and the device settings refs container are frozen: assigning to
one raises, and a ref name that holds several rows holds a tuple of them. Edit
the fields that own the refs instead.

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
