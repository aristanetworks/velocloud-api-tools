# pyvelo

Python SDK for the VeloCloud Orchestrator (VCO) API. Pydantic-typed
client, request/response schemas, enums, models, and a configuration pipeline
with its resolver/validation framework.

[CONFIGURATION.md](CONFIGURATION.md) covers the pipeline (`pyvelo.config`):
the patch format, what a save writes and reports, validators and resolvers.

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
| `pyvelo.core` | `VcoModel` base class (with `.merge_patch()`), layer models (`Layered`, generic over a layer: `Edge`, `Profile`, `Effective` or `Composed`) and their field kinds (`Inheritable`, `Additive`, `Unconverted`, and `EdgeOnly` / `ProfileOnly` subscripted), `Default` (the display default of an `Inheritable` field), `Owned` with its encodings `ByPresence` and `ByFlag` (`Encoding` is either) and its intents `set_local`, `inherit` and `localize`, coupled groups (`MemberOf`, `OwnedGroup`, `GroupHandle`), `bind_layers` (links an edge layer to its profile, which `localize` copies from), `effective_view` (computes `Data[Effective]` from an edge layer and its profile layer: each value a `Resolved` with its `Origin` `EDGE`, `PROFILE` or `FACTORY` and its `source`, read-only), `effective_dump` (the view as wire JSON, and the `Resolved` of each wire path in it) and `Authority` (marks an `Inheritable` field whose effective value is the profile's whenever a rule on the profile's value holds), `field_kind`, `FieldKind` and `Kind` (what a field of such a model is, and the names of its kinds), `kind_default` (the default of an `EdgeOnly`, `ProfileOnly`, `Additive` or `Unconverted` field), `layer_for` (the stored layer a `ConfigContext` level reads and writes) and `unwrap` (the value an `Owned` stores; any other value as it is), field markers (`KeyField`, `FixedList`, `Resolvable` (an identity type and an accessor of it, checked against the field's type at class creation), `NameOf`, `OverrideFlag`, `Alias`, `Normalizer`, `ConfigContext`, `ContextDefault`), defaulting helpers (`DefaultingModel`, `DefaultingScalar`), `Secret` (marks a field that holds a secret), `OwnsRef` and `RefOnly` (mark the data field a ref row goes with, and a field that stands for a ref row and is never dumped or read from data), `Provides` and `RefersTo` (mark a list whose items a set of names refers to, and a field that holds one of them), `HubRole` (marks a list of hubs by the role it gives), `get_owns_ref`, `get_ref_only`, `get_provides`, `get_refers_to`, `get_hub_role` and `get_fixed_list`, `is_secret` and `hide_secrets(model, path, value)` (the value with its secrets shown as `***`), identity types (`EdgeId`, `SegmentId`, `ProfileId`, …; for a hub `HubIdentity`, `HubName`, `HubLogicalId` and `HubService`, with `HubServiceNotFoundError`); the patch helpers (`add`, `delete`, `move`, `at`), `PatchError` and `PatchErrorKind` (the type of its `kind`); `MendingPatch` and `MendingKind` (a patch that mends an error, as JSON data with the `apply` arguments that send it) and `backhaul_hub_mending` (the patch that makes a hub a backhaul hub of a segment, as `HubServiceNotFoundError` gives it); for a tool that works over patches, `Path` and `Key` (the parts of a change's location), `key_text` and `item_key_text`, `list_kind` and `ListKind`, `select_items` and `item_selector`, `key_selector`, `selector_of` and `patch_key` (the key a patch has for one part of a path), `nest_patch` and `address_path` (the patch an addressed call stands for, and its address as one `Path`), `position_of`, `is_positional` and `get_name_of`; facts about a field for a schema view: `wire_name` (the JSON key it is written under), `python_name` (the field a key names; `python_path` in `pyvelo.core.rebase` does the same for the path of a change), `field_models` (the model classes its value, its list items or its map values can be), and `Model.context(level)`, whose entries say whether a field exists at edge or at profile level (`is_present`) |
| `pyvelo.client.module_write`, `pyvelo.client.module_check` | `plan_module` and `ModulePlan` (what a write of a module changes), `check_module_write` and `ModuleWrite` (a write that was stored: the module as stored, the changes `applied`, `left_out` and `server_changes`) |
| `pyvelo.config` | `ConfigPipeline` and `build_default_pipeline` (load → apply → save, and revert), `ConfigState`, `ConfigPlan`, `SaveResult`, the patch helpers and the errors of a patch and a save: see [CONFIGURATION.md](CONFIGURATION.md) |
| `pyvelo.config.resolver` | `Resolver` Protocol (one type parameter, the identity), `BaseResolver`, `ResolverRegistry` (`lookup`, `lookup_by`, `resolve`, `find`, `identities`), `ResolverContext`, `ResolutionError` |
| `pyvelo.config.resolvers.*` | Concrete resolvers: edge, segment, profile, address_group, application, service_group, license, link, network_service, hub |
| `pyvelo.config.validation` | `Validator` Protocol, `ValidatorRegistry`, `@validator` decorator, `ConfigStateLike` Protocol, `ValidationError`, `ValidationResult` |
| `pyvelo.testing` | Test fixtures: `FakeEdge`, `FakeSegment`, `mock_vco_client_factory`, `make_jsonrpc_response`, `make_transport`; `ModuleStore`, a fake VCO that holds configuration module rows and serves their reads and writes (`store_of`, `as_stored`); `network_segment_wire`, one item of a `getEnterpriseNetworkSegments` answer in wire keys, to parse with `NetworkSegment.model_validate` and hand a segment resolver as the VCO gives it (`make_segment_resolver` builds one) |

`VcoClient(host, token)` strips the whitespace around the token. A token that
is empty, or has a line break, a tab, another control character or a non-ASCII
character in it, raises `VcoCredentialsError` when the client is built; the
message does not show the token.

## Resolvers

A key is an `IdentityKey` of an identity type: `SegmentId`, `SegmentName` and
`SegmentLogicalId` are keys of `SegmentIdentity`, and what a key resolves to is
that type. After `await resolver.load(context)` (the pipeline does it for a
state), `registry.lookup(SegmentId(2))` and
`registry.lookup(SegmentIdentity, "Guest")` answer from the loaded tables,
without an event loop; `await registry.resolve(...)` answers the same. A resolver
subclass of `BaseResolver[SegmentIdentity]` overrides `lookup_key` and `_load`;
`load` is final: it runs `_load` and then sets `loaded`, which every resolver has
(the `Resolver` protocol declares `loaded` and `requires_load`; `registry.register`
refuses a resolver that lacks either with `TypeError`). `lookup` is
optional (`BaseResolver`'s serves name lookups), and its `resolve` and
`resolve_key` are final and return what the lookups return.

`registry.lookup_by(SegmentIdentity, lambda s: s.object_id, 7)` finds the first
loaded identity for which the accessor returns the value, whatever the field: one
that has no key type, or that an identity of the same name hides from the name
lookups (an Edge and a hub cluster that share a name). It scans every loaded
identity, raises `ResolverNotLoadedError` when the type has no resolver or its
resolver is not loaded, and `ResolutionError` when no identity has the value
(`No segment has the value 7.`; a UUID is printed as its text).

`registry.find(...)` takes what `lookup` takes and returns `None` where no loaded
identity has the name or the ID, so the name for an ID is
`found.name if (found := registry.find(NetworkServiceLogicalId(u))) else None`; it
still raises for a name several identities share. It raises `ResolverNotLoadedError`
for any resolver that is not loaded (load it with `load_resolvers`), so `None` always
means that no loaded identity has the name or the ID.
`registry.identities(SegmentIdentity)` lists the loaded identities of a type.

A model field that takes a name where it holds an ID declares it with
`Resolvable(SegmentIdentity, lambda i: i.segment_id)`: the identity type and an
accessor that reads the field of the identity whose value the field holds. The
accessor is called once, when the marker is built, on a probe that records what
it reads, so it must read one field of the identity and return it; anything else
(`lambda i: i.segment_id + 1`, two fields, a name that is not a field, the old
string form) raises `TypeError` with the accessor to write. At class creation
the field's type is checked against the field the accessor reads: every type the
identity's field allows apart from `None` must be one the field allows apart from
`None` and `MISSING` (a type fits a field that holds it or a class it subclasses, so
a `StrEnum` fits `str`; a `Literal` value fits a `Literal` field that holds it or a
field of its class, so `Literal["edgeHub"]` fits `str`; a `NewType` counts as its
supertype; a field typed `Any` or `object` is not checked), so a `uuid.UUID` field
over `SegmentIdentity.segment_id` is refused with the accessors that would fit,
instead of failing the first time a name is merged into it.

An ID the loaded tables do not hold raises `ResolutionError` (a `KeyError`), as an unknown
name does, with a message such as `No segment has segment_id 99.`. An ID key returns
the identity registered under that ID, also when another identity of the same name was
registered after it. For `lookup` and `resolve`, a type with no registered resolver
raises `LookupError`, which is not a `ResolutionError`; `lookup_by` raises
`ResolverNotLoadedError` for it. A lookup that needs a load not yet done raises
`ResolverNotLoadedError`. A `ResolutionError` has `name`, `identity_type` (always the
identity class), `candidates` (the names its message lists), `path` (the field, when
the lookup was for a field of a patch or a plan; else `None`) and `wanted` (the type of
service a provider field takes; else `None`). When a patch gives a name for a field that
takes an identity's ID through its resolver, the error starts with the field's path, as a
`PatchError` does; a name for a field that mirrors an identity's name, and a name used as
a list key, raise one without a path.

## Writing a configuration module

`VcoClient.update_module(module, data)` writes a module's data as it was read,
with only the caller's changes applied, and returns the module as stored, of
the class of `module`. When nothing changed it sends nothing and returns
`module` itself. It is `plan_module` (`pyvelo.client.module_write`) followed by
`write_module`.

`plan.render()` is the plan as text: the module's Python name (`device_settings`
for `deviceSettings`), then a line for each change. A line's path names a field
by its Python attribute and an item by its name, in the data as planned (an
item the write removes, as it was read), and an added object has its keys by
attribute name. `plan.named(path)` gives that form for one path of
`plan.changes`. What the plan holds stays in the VCO's names, because it is
what is sent and compared: `plan.name`, the paths of `plan.changes` and
`plan.data`. "What a save writes" in [CONFIGURATION.md](CONFIGURATION.md)
explains the lines.

The `defaults` rules of a QoS segment are fixed: their entries, keys and order
stay as read, and only the `action` of a rule can change. `plan_module` compares
its payload with the module as read, and raises `FixedListError` for a payload
that changes anything else in them (a rule's `match`, a rule added, removed,
renamed or moved, the list built or dropped), whatever made the change: an
attribute assignment, a plain-dict `data` or a patch. It does so at every level,
an edge's raw `defaults` included, and `validate=False` does not skip it. The
error lists each path with its reason. A missing entry is repaired when the
module is written, never built by an edit.

Every QoS segment of a profile holds the template's four `defaults` rules
(`Default-Internet-UDP`, `Default-Internet-Other`, `Default-Any-UDP`,
`Default-Any-Other`), which the VCO does not enforce. `plan_module` pairs the
rules of each segment with them, one to one. For a segment that lacks some, the
plan warns (`defaults_repaired`) and lists them in `ModulePlan.missing_defaults`
(`repaired defaults:` or `missing defaults:` in `render()`). When the module is
written for another reason the plan adds the missing rules from the template, in
template order, each with a new `ruleLogicalId` that the module keeps, so a
re-plan sends the same one; a module with nothing else to write is not written
just to repair, and the next write does. `update_module(...,
repair_defaults=False)` and `plan_module(..., repair_defaults=False)` keep the
warning and leave the rules out. A list the template does not describe (an item
it has no entry for, or a missing name that the list already holds under another
`match`) is left as it is, with a `defaults_not_template` warning. The repaired
rules are no change of the caller's, so `changes` leave them out. The edge level
repairs nothing. `plan_module(..., conditional_backhaul={segmentId: bool})` sets
`action.allowConditionalBh` of a repaired rule to the segment's
`vpn.conditionalBackhaul`; without it, as in `update_module`, a repaired rule
has the template's action.

The segments of a profile are the same in its device settings, QoS and firewall
modules, so one module alone cannot change them. `plan_module` and
`update_module` raise `SegmentSetError` for a profile module whose payload adds
or removes a segment, and nothing is sent. That holds whatever the payload is
built from: a model read from the VCO, a model built by hand, or (for
`plan_module`) a mapping. The error names the segments and says how to do it
instead: a segment is assigned through the pipeline, as
`{"segments": {"Guest": {"_op": "add"}}}` (`pipeline.assign_segment(state,
"Guest")`), which makes its entry in all three modules; removing one from all
three together is not supported. The pipeline records what it adds to a profile
module (`module.additions`, made with `add_segment_entry` and
`add_default_entries`), and `plan_module` plans the module against its raw data
plus those entries. An added entry nobody edited is written exactly as it was
made and plans no change (`ModulePlan.added_segments` names them), while an edit
to it is a change from that entry. The refs still compare with the module as
read, so an added device settings entry adds its `deviceSettings:segment` row.
`plan_module(..., via_pipeline=True)` skips the refusal, for the pipeline, which
plans the three modules together.

The hubs of a profile segment are in the same case. A hub in the hub, bridge or
backhaul list of a device settings segment (`vpn.hub_edges.vpn_hubs`,
`vpn.edge_to_edge_detail.vpn_hubs`, `vpn.backhaul_edges`) works only when its
service has that role, which the VCO's role calls set and a module write does
not. `plan_module` and `update_module` raise `HubListError` for a profile
device settings payload whose members of one of those lists differ from the
module as read, as `(type, logicalId)` pairs (a reorder is no change), and
nothing is sent: the VCO would list a hub no Edge can use, or lose the roles of
one taken out. The error names the segment, the lists and the hubs, and says how
to do it instead: send the change as a patch (`{"segments": {"Global Segment":
{"vpn": {"hub_edges": {"vpn_hubs": {"+": {"_op": "add", "name":
"Hub-A"}}}}}}}`) through `pipeline.apply`, and `pipeline.save`, which sets the
roles as steps of the save. The module and the lists are named as a patch
names them: the text starts `device_settings:`, the call it gives is
`pipeline.apply(state, module="device_settings", patch=...)`, and `error.lists`
holds attribute paths (`vpn.backhaul_edges`). `via_pipeline=True` skips this refusal too.

What a hub is, and what names it, is declared on the models, as the segment
set is, so that the pipeline needs no list of paths of its own:

- `HubRole("hub" | "bridge" | "backhaul")` marks a list of hubs by the role it
  gives (`vpn.hub_edges.vpn_hubs`, `vpn.edge_to_edge_detail.vpn_hubs`,
  `vpn.backhaul_edges`). Each is keyed by the entry's `logicalId`, with its name
  as the label, so a patch or a plan names a hub by logical ID, and by name as
  well where the name is unique. `get_hub_role(field_info)` reads the marker
  of a field.
- `Provides(name, key=...)` marks a list whose items are the things a set of
  names stands for: the device settings `segments` (`segment`), and a segment's
  `vpn.backhaul_edges` (`backhaul_hub`, per segment). `RefersTo(name)` marks a
  field that holds one of them: a LAN network's or an interface's `segmentId`,
  and a QoS rule's backhaul entry. A value that names nothing is a dangling
  reference, which the pipeline's `plan()` refuses (`DanglingReferenceError`,
  in `pyvelo.client.exceptions`, with the patches that mend a hub reference in
  `mending`).
- `HubResolver` (`pyvelo.config.resolvers.hub`) finds an Edge or a hub cluster
  by `HubName` or `HubLogicalId` and gives a `HubIdentity`: its kind (`edge` or
  `cluster`), the `type` of a list entry that goes with it (`edgeHub` or
  `edgeHubCluster`), its logical ID, and its Edge ID or cluster ID. It reads the
  enterprise's Edges and clusters once, with `load()`. A member of a cluster is
  no hub of its own, and a name that an Edge and a cluster share is refused.
  The network service that stands for a hub in one profile and segment, which a
  QoS rule's backhaul row points at, is a `HubService`
  (`pyvelo.client.hub_services` names the three ref rows,
  `deviceSettings:vpn:edgeHub`, `deviceSettings:vpn:edgeHubCluster` and
  `deviceSettings:backHaulEdge`).

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
module with no `version` is not written: `VersionRequiredError`, unless the plan
is `unconditional` (`save(..., unconditional=True)`). A write that sends no refs
makes no extra request.

`check_module_version(module, plan)` makes that read on its own, for a caller
that changes other objects before it writes the module (a save that sends hub
role calls): it answers `None` when the version is the one read, the
`ModuleWrite` when the module there already holds the plan's changes, and
raises `ConfigConflictError` with nothing sent otherwise; it answers `None`
without a request for a module with no `version`, and for a plan that sends no
refs. The write then passes `version_checked=True` and makes no read of its own
(`rebase` still reads, because that read decides the rebase).

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
A plan that adds or removes a ref row is rebased too: its rows are derived
again from your edits against the refs just read, so a row you add that is
there already is not added twice and a row you remove that is gone is no
clash. Pass the `resolvers` and `profile_device_settings` the plan was made
with for the rows your edits add (a QoS rule's backhaul hub finds its service
in the profile's device settings). An insert is not rebased.

With `if_absent=True` an insert first reads the configuration, and a module of
that name found there is taken as the insert's answer and checked like one, with
nothing inserted: the way to repeat an insert whose answer never arrived. That
read already holds the refs, so the module found is not read again.

`pyvelo.testing.ModuleStore` is an `httpx` handler that holds module rows as
JSON and answers module updates, inserts, `getConfiguration` and, for an edge
whose two configurations it was told (`add_stack`), `getEdgeConfigurationStack`,
as a VCO does. For an edge whose composed modules it holds (`composed[edge_id] =
{"firewall": data}`, or `None` for the `null` of a module that does not
compose) it answers `getEdgeConfigurationModules` and records the request, so a
test can check `encryptKeys`. Its knobs are the conditional version check, a VCO
that ignores `returnData`, the enterprise a request has to name, writes that
fail, writes that are stored and whose answer is lost (`lose_answers`), rule IDs
issued as a VCO issues them (`issue_rule_ids`), hooks that change what is stored
(`on_store`) and another writer's change (`other_writer`). A client over it is
`VcoClient(host, token, transport=make_transport(store))`: it runs its own
request building, parsing and error mapping against the store. A client given a
`transport` is connected from the start and needs no `async with`; inside one it
keeps the transport, and after it the client is closed.

The store also holds hubs: `add_edge` an Edge (optionally a member of a cluster)
and `add_cluster` a hub cluster. It lists the Edges
(`getEnterpriseEdgeList`), lists the services with the `configuration` and
`edgeQOSUsage` lists, and answers the twelve hub role calls as a VCO does: an
Edge enable makes the `edgeHub` service and its device settings row once, an
Edge hub disable deletes the service and every row that points at it, and an
Edge disable that finds no service answers `null` (the three Edge disable
wrappers return `None` for it; the cluster ones raise `VcoEmptyResultError`).
With `vpn_check` on, a profile device settings update whose Branch-to-Hub list
and hub rows disagree is refused with `DS00000010`, `DS00000011` or
`DS00000020`; `fail_calls` fails a named method once.

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

A field of the first kind is declared with `OwnsRef(name, Identity, by=lambda i:
i.logical_id, row=...)`: `by` is an accessor, probed and checked against the
field's type as `Resolvable`'s is.

A row for a new provider is added only for a service of the type its ref
names. `SERVICE_TYPES` in `pyvelo.core.identity` gives it: `dns` for
`deviceSettings:dns:primaryProvider` and `deviceSettings:dns:backupProvider`,
`authentication` for `deviceSettings:authentication`, and `cloudSecurityService`
for `deviceSettings:css:provider` (tags of `EnterpriseServiceType`).
`service_type_of(model, field_name)` in `pyvelo.core.fields` gives the type one
field takes. A service of another type raises `ResolutionError` that names the
field, the service, its type and the wanted type (the error's `wanted` is that
type and its `candidates` the services of it), and gives the patch that sets the
field; a ref name outside that table is not checked.

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
(`ResolverNotLoadedError` when it is missing). A QoS rule's new backhaul hub
adds a `deviceSettings:backHaulEdge` row that points at the hub's service for
the profile and segment, which the profile's device settings module holds: give
it as `profile_device_settings=` (`HubServiceNotFoundError` when the hub has no
service there, or none is given). A device settings or QoS update with segments
always carries its refs, as read when nothing in them changed, because the VCO
runs its processors on every such write. A module read without its refs
(`refs_loaded` is `False`) cannot be written that way, nor with an edit to one
of these fields: `RefsNotLoadedError` says to read it with
`configuration/getConfiguration` and `modules`, or from the edge stack. The ref
names that are not derived are written with `update_configuration_module`, the
low-level request.

`module.refs` is read-only, because the write ignores it. `module.refs = ...`
raises a `ValidationError`, and the rows (`ConfigurationRef` and its
subclasses) and the device settings refs container are frozen: assigning to
one raises, and a ref name that holds several rows holds a tuple of them. Edit
the fields that own the refs instead.

## Layers and ownership

A configuration module is read at two stored layers, and a model that is
`Layered` has one class for each: `FirewallData[Edge]` holds what an edge
stores, `FirewallData[Profile]` what a profile stores, `FirewallData[Effective]`
(computed by `effective_view`, read-only) what the edge shows once its profile
is applied, and `FirewallData[Composed]` what the VCO composes for an edge
(`edge/getEdgeConfigurationModules`, read-only). The firewall and QoS modules
are layered so far: `QosData[Edge]`, `QosData[Profile]` and
`QosData[Composed]`, with `QosSegment` and `QosConfigurationModule` typed the
same way; a segment's `web_proxy` is the one QoS field still `Unconverted`
(see "QoS"). A model needs its layer: a class
without one raises `TypeError` when it is validated or built. Each field of
such a model has a kind, which says what it is at each layer:

| Kind | Edge | Profile | Meaning |
|---|---|---|---|
| none (structural) | the value | the value | the same at both layers (`segments`, a segment's `segment`) |
| `Inheritable[T]` | `Owned[T]` | `T` | the edge may override what the profile provides |
| `Additive[list[T]]` | `list[T]` | `list[T]` | the edge's items come ahead of the profile's, which are not copied into the edge |
| `EdgeOnly[T]`, `ProfileOnly[T]` | `T` or absent | absent or `T` | the field belongs to one layer |
| `Unconverted[T]` | `T` | `T` | inheritance not declared yet: as the field has always been |

`Owned[T]` is what an `Inheritable` field holds on the edge layer. `stored` is
what the edge holds at the key (a value, `None` for a stored JSON `null`, or
`MISSING` when the key is absent); `is_local` says whether that counts as the
edge overriding the profile (for the firewall: the key is present and not
`null`, so `false`, `0` and `{}` are local); `local` is `stored` when it is
local and `MISSING` otherwise. A `null` is kept as read and does not count as
local, so an unrelated edit still sends it as it was. An assignment to the
field (`data.syslog_forwarding = True`) raises `TypeError`, because it would
lose who owns the value. An `Owned` is not a condition: a test of it
(`if data.syslog_forwarding:`) raises `TypeError`, as it would be true whatever
the edge holds, and `data.syslog_forwarding == True` is `False`, as an `Owned`
equals only another `Owned`. Read `local`, `stored` or `is_local`; the value
the edge runs with is `state.effective.firewall.syslog_forwarding.value`, and a
`Resolved` is not a condition either. There are three intents, each with the
patch a text-only caller sends for it (`ConfigPipeline.apply(state,
module="firewall", patch=...)`, or `data.merge_patch(...)`):

| Python | Patch | What it does |
|---|---|---|
| `data.syslog_forwarding.set_local(True)` | `{"syslog_forwarding": true}` | the edge overrides the profile with this value (`false` too); a `null` is refused: it means inherit |
| `data.syslog_forwarding.inherit()` | `{"syslog_forwarding": null}` | the edge stores nothing, so the profile's value applies |
| `services = data.services.localize()` | `{"services": {"ssh": {"enabled": true}}}` | the profile's value (or the display default, when the profile stores none) is copied in as the edge's own and returned to be edited in place; a patch into a section the edge inherits does this first, then applies the patch |

`set_local` validates the value as a patch value is validated and stores a
copy. `localize` needs the profile when it copies (an already-local value, or a kept `presence_wins` flag-off object, needs none), so it works on objects taken from a loaded
`ConfigState` (`state.edge.firewall`), which binds the edge layer to the profile
layer; for a module read by hand, `module.data.bind_profile(profile.raw_data)`
does the same. A segment `data.segment(name)` adds to a bound edge layer is bound to the profile's segment of that ID, so its keys localize from it too, and a copy made local carries no server-assigned ID of the profile's object. After a save or `update_module` replaced the module an `Owned`
belongs to, its intents raise `DetachedObjectError`; reading `stored` does not.

### The firewall

The module-level values of the firewall are `Inheritable` and stored by
presence. Their wire keys are as they have always been (`firewall_enabled`,
`firewall_logging_enabled`, `syslog_forwarding`, `stateful_firewall_enabled` and
`atp_enabled` in snake case; the rest in camel case).

| Field | Group | Display default |
|---|---|---|
| `firewall_enabled` | | `true`; the effective value is `false` whenever the profile's is |
| `firewall_logging_enabled` | | `false` (`null` is a stored value that is not local) |
| `syslog_forwarding` | | `false` |
| `services` | | the template's `services`; the whole object is local or inherited, its children are never filled from the profile |
| `stateful_firewall_settings`, `stateful_firewall_settings_v6`, `stateful_firewall_enabled` | `stateful` | the template's settings; `false` |
| `network_protection_settings`, `network_protection_settings_v6` | `network_protection` | the template's settings |
| `atp_enabled`, `security_features` | `atp` | `false`; the template's features |

`inbound` and `inbound_v6` are `Unconverted`, as is a segment's
`firewall_logging_enabled`. A segment's `outbound` is `Additive`: the edge's
own rules, in the order the caller gives them, with no profile rule copied in.

The fields of a group change ownership together, because a group with some
members inherited and others local is a state the VCO UI does not recognize.
On the edge layer `data.stateful`, `data.network_protection` and `data.atp` are
handles with `is_local` (the group's anchor member is local), `inherit()`,
`set_local(bundle)` and `localize()`, each for every member at once:

- `data.stateful.inherit()` removes the three keys; the patch is
  `{"statefulFirewallSettings": null}` (the anchor; the others follow).
- `data.stateful.set_local(StatefulLocal(...))` makes all three local from a
  bundle that names every member (`StatefulLocal`, `NetworkProtectionLocal`,
  `AtpLocal`, from `pyvelo.client.models.configuration`); the patch names every
  member.
- `data.stateful.localize()` makes all three local, copied from the profile; a
  patch that sets one member of a group the edge inherits does the same first,
  and then sets that member (`{"stateful_firewall_enabled": false}` stores the
  profile's settings with `stateful_firewall_enabled` false). Other members are
  then edited through their own `Owned`. Making a group local copies every
  member from the profile: a member the profile holds `null` for starts from
  its default, and a member that has neither is refused with
  `GroupOwnershipError`, with nothing changed. To make such a group local on
  the edge alone, send one patch that gives that member a value along with the
  others. This works unless that member is the group's anchor: the group is
  local only once its anchor is, so only a value in the profile helps then.
- A member alone refuses `inherit()`, and refuses `set_local()` while the group
  is inherited, with `GroupOwnershipError`, which says to use the group. Once
  the group is local, `data.stateful_firewall_enabled.set_local(False)` keeps
  the settings as they are.

The profile layer has plain values and no group handles. A key it does not
store reads as its display default and is not written: the profile's dump
writes a field only when it was read from data, assigned, or changed in place
to something other than the default. So `profile.data.atp_enabled = False`
on a profile whose raw data lacks the key writes `"atp_enabled": false`.

A display default is what a value is when nothing stores it: the profile
layer's reading of an absent key, and the `FACTORY` origin of an effective
value. It comes from the profile template the library ships
(`new_segment_templates.json`, its `firewall.module` part, read by
`module_template_value("firewall", key)` in
`pyvelo.client.models.configuration.profile_segments`), so an untouched profile
displays `stateful_firewall_enabled` and `firewall_logging_enabled` as `false`
and `networkProtectionSettings.icmpBasedAttacks.enablePingOfDeath` as `true`.
A `FACTORY` value is never written: nothing is sent for a key the caller did
not set, whatever it displays.

SSH access (`services.ssh`, an `SshService`) has one allow list,
`allow_selected_ip`, which holds IPv4 and IPv6 addresses alike. SNMP and the
local UI have a second list for IPv6, `allow_selected_ip_v6`. `ssh` has no
`allow_selected_ip_v6`, because the Edge reads none for SSH: a patch that names
it is refused as an unknown field, so put IPv6 addresses for SSH in
`allow_selected_ip`. A stored `allowSelectedIpV6` on SSH is kept as read by
every write, like any key the models do not know.

The ICMP service (`services.icmp`) is not modeled, because the Edge does not use
it. No field reads it and a patch that names it is refused as an unknown field.
A stored one is kept as read by every write, like any key the models do not
know, and `compare_effective` reports one the VCO composed as `unconverted`. A
`services` object the library makes itself carries no `icmp`: neither the
display default nor a copy made local by `localize()` (or by a patch into an
inherited `services`) has one, even where the template or the profile stores
one. To change or remove a stored one, plan the module from the data as a
mapping, as for `qosClientConnector` below.

### QoS

The module-level values of QoS are `Inheritable` and stored by presence, as the
firewall's are, with the template's display defaults
(`module_template_value("QOS", key)`); none of them is in a group. Their wire
keys are `serviceRateLimit` and `qoe`.

| Field | Display default |
|---|---|
| `service_rate_limit` | the template's: disabled, `percent`, `1`. A disabled limit the edge stores is local |
| `qoe` | the template's latency thresholds |

`qosClientConnector` (an edge's Client Connector settings) is not modeled. No
field reads it and a patch that names it is refused as an unknown field. A
stored one is kept as read by every write, like any key the models do not know,
and `compare_effective` reports one the VCO composed as `unconverted`. To
change or remove it, plan the module from the data as a mapping, with the key
changed or left out, and write the plan: `plan = plan_module(module, data,
level=ConfigContext.EDGE)`, then `await client.write_module(module, plan)`
(`ConfigContext.PROFILE` for a profile's module). (`update_module`
takes a model, not a mapping.)

A segment's fields are these kinds:

| Field | Kind | Meaning |
|---|---|---|
| `rules` | `Additive` | the edge's own rules, in the order the caller gives them; the profile's are not copied in, and the effective view lists the edge's first, then the profile's |
| `defaults` | `ProfileOnly` | the profile's four fixed rules (see "Writing a configuration module"); an edge segment holds none, and a write keeps the `defaults` the edge's data had as read. A `merge_patch` naming a `ProfileOnly` field on an edge layer raises `PatchError` (the VCO would add an edge's to the profile's): patch the profile layer |
| `cos_mapping` | `Inheritable`, by flag | see below |
| `web_proxy` | `Unconverted` | as it has always been |

`cos_mapping` is stored by its flag, `override`. The object is local when it is
present and `override` is true, so `set_local(CosMapping(...))` writes it with
`override: true` (the flag follows the value; a patch that names `override` is
refused), and `inherit()` removes the object. An edge object stored with
`override: false` is kept as read and is not local, yet the VCO's composer takes
a present edge object without reading its flag, so the effective view shows it
as the edge's (`Origin.EDGE`). `plan_module` on an edge module warns of each
one (code `flag_off_in_use`, at `segments["Global Segment"].cosMapping`),
whether or not the write changes anything, so nobody reads `is_local == False`
and believes the edge inherits. Remove it with `inherit()` for the profile's
mapping to apply. To keep it, `localize()` turns its flag on and changes no
value: it returns the stored object, not the profile's, and a patch into it
changes only what the patch names. `{"segments": {"Global Segment":
{"cosMapping": {}}}}` does the same from text. When the edge stores nothing at
the key, `localize()` copies the profile segment's mapping (or the display
default, `CosMapping()`) in with `override: true`; on a segment
`data.segment(name)` added it copies from the profile's segment of that name.

| Python | Patch |
|---|---|
| `segment.cos_mapping.set_local(CosMapping(...))` | `{"segments": {"Global Segment": {"cosMapping": {...}}}}`, when `{...}` is the whole object: a partial patch merges into the object `localize()` gives (the profile's mapping, or the object the edge stores with its flag off), so it keeps every key it does not name |
| `segment.cos_mapping.inherit()` | `{"segments": {"Global Segment": {"cosMapping": null}}}` |
| `data.service_rate_limit.localize()` | `{"serviceRateLimit": {"value": 50}}`: the profile's limit, or the display default when the profile stores none, is copied in whole and then edited |

A value made local from a display default is written whole: the edge stores
every key the template has (`enabled`, `inputType` and `value`), not only the
one the caller changed. A profile that stores no `serviceRateLimit` reads the
display default, and a profile layer writes only the keys it read or the caller
set.

## Portal list and pagination methods

| Method | Use for | Source requests |
|---|---|---|
| `portal_list` | Non-paginated list endpoints | One. A complete paginated envelope is unwrapped; an incomplete one raises. |
| `portal_paginated` | Callers that control paging | One per yielded page. |
| `portal_paginated_list` | Paginated endpoints returning a list | Until the last page, or `request.max_pages` pages when it is non-zero. |

Both paginated methods record `page_has_more` and `page_next_link` on the
caller's request after every page; the caller's `next_page_link` is not
modified. After a capped `portal_paginated_list`, `page_has_more=True` marks
the result incomplete and `page_next_link` resumes from the next page. A page
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
uv run basedpyright packages/pyvelo
```

`packages/pyvelo/tests/test_layer_typing.py` is a typing module: its `_cases`
function is never called, and basedpyright is its test. Each `assert_type` is a
cell of the kind table in "Layers and ownership" (what a field gives on each
layer), and each `# pyright: ignore` marks an access the table forbids. The
module turns on `reportUnnecessaryTypeIgnoreComment`, so an ignore that hides
no error is an error too, and a change that makes a forbidden access legal
fails the check.
`packages/pyvelo/tests/config/test_layer_typing.py` does the same for the layer
a `ConfigState` gives (`state.edge`, `state.profile`).
