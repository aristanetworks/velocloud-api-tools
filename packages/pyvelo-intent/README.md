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
Calling `save(state)` sends configuration writes, and the state then continues
from what the VCO stored (see "After a save"). Check `SaveResult` with
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
must be a `dict`.

A patch is strict and all or nothing:

| Value | Field | Effect |
|---|---|---|
| object | holds a sub-model | merged into it; built from the patch when unset |
| object or list | a list | item operations, or a list value that replaces the list (below) |
| object | takes one sub-model or a list of them, and holds no list | merged into the sub-model; built from the patch when unset. A list value makes the field a list |
| object | a map of models (`dict[K, Model]`) | item operations by map key |
| `null` | type includes `MISSING` | unset |
| `null` | type includes `None`, not `MISSING` | `None` |
| `null` | neither | `PatchError` |
| anything else | any | replaces the value |

- Every value is validated as the field is when a response is parsed. Fields
  that tolerate a bad value from the VCO (a `null` or an empty string turned
  into the default) do not in a patch: it raises. This holds for an item or
  sub-model a patch builds as for one it changes, and so do the three `null`
  rows above: `add({"name": "New", "advertise": None})` raises, because
  `advertise` can be neither unset nor `None`. One case differs: a field
  whose type includes both `MISSING` and `None` is unset by `null` in a
  change, and is `None` in an item a patch builds, where leaving the key out
  leaves it unset. So the JSON dump of an item can be given as the body of an
  `add` and builds the same item.
- `true` and `false` are taken only where the field holds a boolean. A boolean
  for a number raises: `{"vlan_id": true}` is not VLAN 1.
- A patch must leave every model it changes valid. After the merge, the
  `after` and `wrap` model validators (`@model_validator`; not the `before`
  ones) of each changed model run, and an item of a list of several model
  types must still be the type its fields select. A change that fails raises
  `PatchError` at that model: a patch that deletes every netflow collector
  while `enabled` stays true raises at the netflow block (set `enabled` in
  the same patch), and so does renaming LAN interface `GE1` to `WLAN1`. An
  item or sub-model built whole from a patch is validated whole.
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
item of a keyed or positional list (in a list of values and in a map, `add`
goes under the new value or key itself). Under it, a plain object changes the
item (and may rename it to a key no other item has), `add(body, ...)` adds
one, `delete()` removes it, and `move(...)` moves it. A place is one of
`first=True`, `last=True`, `before=selector` or `after=selector`; an `add`
without one goes last. A selector that names no item, or more than one,
raises and lists the matches; `add` of a key or value already in the list
raises. An alt key is kept unique the same way: an `add` or a change that
gives an item an alt key another item has (a rule's logical ID, a segment's
`segmentId`) raises, and so does a list value that repeats one. Every
selector is resolved against the list as it was, then changes, deletes, and
adds and moves (in the order written) are applied.

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

A patch holds only what `json.loads` returns: dicts, lists, strings, finite
numbers, booleans and `None`, nested at most 64 levels deep. Anything else
raises `PatchError` at its place in the patch: a tuple, a set or a generator
where a list goes, another mapping type where a dict goes, an object such as
a `UUID` where its text goes, `NaN` or an infinity. The one exception is a
selector, which as a dict key may also be an `int`, a `UUID` or the tuple of
a composite key's parts. A patch written in Python is JSON too: `None`
unsets a field, not `MISSING`, and a model is given as its JSON-mode dump
(`model_dump(mode="json")`), not as the object. `apply` merges a copy of the
patch, so the state shares no object with it: the same patch can be applied
to another state, or changed, afterwards.

The `segments` lists have rules of their own:

- On an edge, a segment the edge inherits from its profile counts as there. A
  change or a move of one the edge layer lacks first adds its placeholder
  entry; `add` of an inherited segment raises (write a plain object), and so
  does `delete` of one the edge layer has no entry for. Where the entry of a
  segment the edge only inherits is written does not follow a move: see "What
  a save writes".
- On a profile, `apply` refuses `add` and `delete` of a segment: a segment
  belongs to all three of device settings, QoS and firewall, and adding one
  through a patch is not supported yet. Changing or moving one is fine.

Sections and items of an edge that can follow the profile have an `override`
flag: the BGP of a segment, a VLAN, an interface. While the flag is off, the
edge takes that section from its profile. A patch that changes a field of
such a section turns the flag on; a field that is always the edge's own, such
as an interface's address or `description`, does not. From that save on, the
edge layer's own section applies, as it is stored there and with your change,
and the profile's no longer does. Nothing is copied from the profile. So
adding one BGP neighbour to an edge that inherits BGP leaves that edge with
the one neighbour and the edge layer's own `enabled` and ASN, not with the
profile's neighbours plus one. Give every field the section needs in the same
patch; `state.profile` holds the profile's values. The plan shows the flag as
a change and names the section in a line of its own:

```text
deviceSettings: update
  ~ segments["Global Segment"].bgp.override: false -> true
  + segments["Global Segment"].bgp.neighbors["10.0.0.2"]: {...}
  override turned on: segments["Global Segment"].bgp (the edge's own values apply there from now on, and the profile's no longer do)
```

`ModulePlan.overrides_turned_on` holds the same paths. A flag inside a list
that is written whole (an interface's `subinterfaces`) is part of that list's
change and has no line of its own. When the section as read has no `override`
key, the flag cannot be written: the same patch is refused with
`LossyFieldError`, which names the flag.

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
  since, the save raises an error that lists your lost edits (see "After a
  save").
- A save asks the VCO for each module it writes, as stored, and the state
  continues from it, so no reload is needed. Rule IDs the VCO assigns are in
  the state after the save, and `state.plan()` right after a save is empty.
  The state takes more edits and saves.
- `state.plan().render()` shows each module's changes against the raw data as
  read, as in the example above. `SaveResult.plan` is the same plan, and
  `dry_run=True` reports it without writing.
- When the profile has segments the edge module lacks, a write to that module
  adds them as entries that inherit everything, after the edge's own entries
  and in the profile's order. The plan lists them under "reconciled segments".
  Looking one up (`state.edge.firewall.segment("Voice")`) or editing it does
  not change where it is written, and a lookup alone writes nothing. A `move`
  of such an entry changes its place in the state only.
- Deleting an edge segment that the profile also has leaves an empty entry for
  it in the write, as for a segment the edge never had.
- A missing edge firewall or QoS module starts empty; nothing is copied from
  the profile. It is inserted once it differs from that empty layer: patches
  that cancel out leave nothing to insert.
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
- A list is written item by item when its items can be told apart, so only the
  items you changed differ from the data read. That is a keyed list (see "The
  patch format") in which every item has a key no other item has, or an ID
  that was read and no other item has (a rule's logical ID, a segment's
  `segmentId`). Two firewall rules of one name, each read with its
  `ruleLogicalId`, are two items: deleting one leaves the other as read. Any
  other list is written whole from the models when something in it changes: a
  positional or untyped list, and a keyed list that holds an item with no key
  and no ID (a WAN link read without an `internalId`) or two items of one name
  read without IDs. Keys the models do not know are then dropped from every
  item of that list, and the models' defaults are written into them. A list
  inside an interface that is not a plain port (a LAG's `slaveInterfaces`) is
  also written whole when something in it changes, and keys the models do not
  know are dropped from its other items.
- A patch that moves items (a `move`) shows in `plan()` as one change at the
  list; in a keyed list the items' unknown keys are still kept.
- A cache of loaded modules must keep the JSON rows or `module.raw_data`. A
  `model_validate(module.model_dump())` round trip loses the raw snapshot, and
  with it the "as read" guarantee above.

## After a save

Each module a save writes is replaced in the state by the module as the VCO
stored it. `SaveResult` also reports, per module, what the VCO did beside
storing your changes. Neither list is an error.

- `server_changes` is what the VCO stored otherwise than sent, at paths that
  are not lost edits. It lists keys the VCO added, IDs it issued or revised (a
  firewall rule's `ruleLogicalId`, a WAN link's `internalId`), values it keeps
  in its own form (an IPv6 address in canonical form), the default it added
  back where you removed one, and what another writer changed beside your
  edits. A value stored as it was read is not listed here.
- `left_out` is the planned changes the VCO stores its own way, which are
  neither applied nor lost. They are a change to a field the VCO owns (a
  firewall rule's `ruleLogicalId`), the removal of a default it adds back, and
  the removal of a row of an append-only ref (an address group, for example). Each
  has the value read (`as_read`), the value sent (`intended`) and the value
  stored. Saving again would not store them either, so they are not to be
  redone. After a refusal such a change is a lost edit instead, when it is
  still as read: see below.

A write that is not known to be stored as planned raises one of three errors,
from `pyvelo.client.exceptions` and re-exported by `pyvelo_intent`.
`ConfigConflictError` says the VCO refused the write because the module changed
since it was read, or seems to have (`stated`, below, says which);
`WriteNotAppliedError` says the VCO stored the write otherwise than as sent.
`WriteOutcomeUnknownError` says the write was sent and the module as stored
could not be read. When the read itself fails, its own error is raised, and the
write may be stored.
The rows below are tried in order, and the first that fits is raised, so a
refused version is a `ConfigConflictError` even when some of your changes are
stored:

| When | Raises |
|---|---|
| the module as stored could not be read back, or the VCO's answer is not this module's row | `WriteOutcomeUnknownError` |
| the VCO refused the version, and the module as stored does not already hold every one of your changes | `ConfigConflictError` |
| the write is an insert, which carries no version | `WriteNotAppliedError` |
| the VCO answered with the module, none of your changes is stored in it, and each value there is as read | `ConfigConflictError` |
| anything else: some of your changes are stored and some are not, a value you edited is stored as neither the one read nor the one sent, or the VCO counted the write and the module read back does not hold it | `WriteNotAppliedError` |

A refused write passes only when the module as stored already holds every one
of your changes: at least one is found stored, and none is lost or left out.
That is a write sent twice whose first copy was stored. After a refusal, a
change that would be left out and is still as read is in `lost_edits`, because
the refused write did not reach it. So is one beside changes of which none is
stored, when the VCO answered with the module.

`ConfigConflictError.stated` says how the refusal is known. True: the VCO said
so, with its precondition error or a row count of zero. False: the VCO answered
with the module, and the save inferred the refusal from it; the message then
says "seems to have refused".

Which of the two a stale write raises depends on the VCO's conditional check
of the version. With it on, the VCO refuses the write, and the save raises
`ConfigConflictError`. With it off, the VCO does not store the data and
answers with the module as it is. The save then raises `ConfigConflictError`
too, unless another writer also changed a value you edited: that raises
`WriteNotAppliedError`, with the edit marked as a clash. A write that carries
refs (an edited `module.refs`, or `refs=` on `update_module`) may still replace
them, until the next phase reads the version before such a write.

A VCO with the check off says nothing when it answers with the module, so the
save tells a stale write by what is stored. Where no change of a write can be
found stored (it changes only fields the VCO owns, removes only defaults the
VCO adds back or rows of an append-only ref, or changes only the inside of ref
rows), the save does not ask for the module. The VCO then answers with a row
count, which is zero for a stale version, and the save reads the module back.
Such a write costs one more request.

What a save cannot tell, on a VCO with the check off: a stale write whose other
changes are all stored already, by another writer or by an earlier save of
yours whose answer was lost, passes. A left-out change beside them is then
reported in `left_out` although the write that carried it was not stored. Look
at `left_out` when a save follows one that failed.

`ConfigConflictError` and `WriteNotAppliedError` carry:

| Attribute | Holds |
|---|---|
| `lost_edits` | the planned changes that are not stored: each with its `path`, the value `as_read`, the value `intended` and the value `stored`; `clash` is true where the stored value is not the one read, because something else changed it too |
| `applied` | the planned changes that are stored |
| `left_out` | the planned changes the VCO stores its own way, as in `SaveResult.left_out` |
| `server_changes` | what else the VCO stores otherwise than sent, as in `SaveResult.server_changes` |
| `module` | the module as the VCO holds it now |
| `plan` | the module's `ModulePlan` |
| `stated` | on `ConfigConflictError`: whether the VCO said it refused the version, or the save inferred it |
| `completed` | the writes of the same save before this one, which are stored |

The entries are `PlannedChange` and `ServerChange`, from
`pyvelo.core.response_check`. The message lists them by path. Here, a change
to a rule's port and a new rule are refused, and another writer had changed
the same port and another key:

```text
Configuration module 11 (firewall) changed since it was read (version v1): the VCO refused the version
lost edits (not stored; redo these):
  segments["Global Segment"].outbound[block-ssh]: read (nothing), meant {"action": {"allow_or_deny": "deny"}, "comments": null, "loggingEnabled": false,…, stored (nothing)
  segments["Global Segment"].outbound[allow-web].match.dport_high: read 443, meant 8080, stored 8443  [clash: also changed on the VCO]
stored otherwise than sent:
  syslog_forwarding: read false, stored true
```

After either of those two errors the state holds the module as stored, so your
edits to that module are no longer in it. Redo the edits in
`error.lost_edits` and save again; no reload is needed. Redo only those: an
`add` of an item that is already stored raises `PatchError`. A clash is where
the redo overwrites another writer's change. The modules written before the
failing one are stored and in the state (`error.completed`). Any other error of
a request (a transport error, for example) leaves that module and the later
ones with your edits, and carries a note that names the modules written before
it; a second save sends what still differs.

`WriteOutcomeUnknownError` has no lists and no module. `error.answer` says what the VCO said of the write itself: `"refused"` (it refused the version, so this write stored nothing), `"counted"` (it counted the write, so the changes are most likely stored) or `"row"` (its answer could not be read as this module). `error.detail` says why the module could not be read, and `error.plan` holds what was sent. The state keeps the module as read, with your edits. For an update, saving again is safe: if the first write was stored, the VCO refuses the old version, the save reads the module and reports what is stored. For an insert, look at the configuration first.

A `WriteNotAppliedError` after a write that did work means the VCO stores that
value in another form than the one sent. When the value is part of the key of
an item you added, the item looks absent instead, and what the VCO stored looks
like another writer's item: with nothing else changed, that reads as a
conflict, `ConfigConflictError`, and redoing the `add` stores the item twice.
The fix is an entry in the table of rewrites, `MODULE_REWRITES` in
`pyvelo.client.module_check`, or a `ServerOwned` marker on the model's field.
Until then every save of that value raises.

Objects taken from the state before a save
(`seg = state.edge.firewall.segment("Global Segment")`) belong to the modules
the save replaced, and are detached. Reading one is fine. An assignment to one
raises `DetachedObjectError`, and an in-place edit to a list or dict it holds
(`seg.outbound.insert(0, rule)`) is reported, once, by the next `plan()` or
save. Take the object from the state again and redo the edit.

Known limits:

- A plain list or dict taken from a module before a save and held on its own,
  after every model of that module is dropped, is not checked for in-place
  edits. Hold the model, or take the list from the state again.
- A lookup through a detached layer that adds a placeholder
  (`fw.segment("Guest")` on a `fw` taken before the save) is reported by the
  next `plan()`. Take the layer from the state again.
- A save that changes a profile's device settings and its QoS (or firewall)
  together may need a second save, when the device settings change propagates:
  the later module's write raises with your edits in `lost_edits`, and the
  state holds that module as stored, so redo them and save again. Rebasing
  those edits comes with derived refs, the next phase.
- Until then, device settings and QoS writes send refs only when your refs
  changed, as before.
- On a VCO with the conditional check off, a stale write that carries refs may
  replace them while the data is not stored, overwriting another writer's
  refs; the save then reports your data changes, if any, as not stored. Reading the version
  before a write that carries refs comes with the next phase.
- A keyed list read with an item that cannot be told apart is compared as a
  whole: an item with no key and no ID (a WAN link without an `internalId`),
  or two items of one name read without IDs. A change in it to a field the VCO
  owns is then part of the list's change, not in `left_out`, and a value stored
  there otherwise than sent is a server change with no value read (`as_read`
  is `MISSING`), even when it is the value read. A key you removed from an
  item of such a list counts as stored only when the stored item at that
  position no longer has it. If the VCO puts the key back on its own, every
  save that changes that list raises `WriteNotAppliedError` until the key is in
  the table of rewrites. An edit that shifts the items of such a list
  (removing or adding an item before others) can raise it too.
- An update with no change the check can find stored is sent without
  `returnData` and a row count of zero is taken as a refusal, so if a VCO
  counted zero rows for a write it stored that changed nothing in effect (not
  measured), that save raises a stated `ConfigConflictError` every time the
  edit is redone.
- A key the VCO stores in a form the table of rewrites does not cover reads as
  your item lost and another item, only the VCO's, in `server_changes`. IPv6
  addresses in the keys of device settings (a static route's destination and
  gateway) are covered.
- An insert that reached the VCO but whose save failed, for example with a
  transport error, is retried as an insert by the next save. Check the
  configuration before saving again.

## Scope and data handling

- Updates support firewall, device settings, QoS, and WAN modules. A missing
  edge firewall or QoS module can be created; it starts empty and is inserted
  on save. Control plane helpers support inspection and in-memory patches,
  but the save path does not support writing control plane modules.
- A failed `apply` changes nothing. Patches and saves run one after another,
  though: a failure leaves earlier patches, and the remote writes of a save
  that stopped part way, applied; there is no transaction or rollback.
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
| `pyvelo_intent` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, the patch helpers (`add`, `delete`, `move`, `at`) and `PatchError`, diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), the intent error types, the write errors `ModuleWriteError` (the base of the next three), `ConfigConflictError`, `WriteNotAppliedError`, `WriteOutcomeUnknownError` and `DetachedObjectError` (from `pyvelo.client.exceptions`), helpers (`build_default_pipeline`, `confirm_save`, `load_config`) |
| `pyvelo_intent.config.pipeline` | `ConfigPipeline` (load/load_profile → apply → save), `build_default_pipeline` |
| `pyvelo_intent.config.state` | `ConfigState`, `SaveResult`, `ConfigFieldChange`, `ValidationWarning` |
| `pyvelo_intent.config.helpers` | `load_config`, `confirm_save` |
| `pyvelo_intent.config.validators.*` | Structural built-ins (`key_uniqueness`, `override_consistency`, `required_fields`) + domain validators (firewall, device settings) |
| `pyvelo_intent.config.(firewall\|device_settings\|qos\|wan\|control_plane)` | Per-module configuration helpers |
| `pyvelo_intent.diff` | Generic keyed-list diff engine: `diff_intents`, `DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange` |
| `pyvelo_intent.errors` | `IntentError`, `ConfigInputError`, `SaveValidationError`, `UnknownRegistryEntryError` |
| `pyvelo_intent.testing` | Test fixtures: `MockVcoClient`, which holds a `ModuleStore` (`pyvelo.testing`) as `store` that serves module reads and writes (`MockVcoClient.for_state(state)` seeds it from a state), `make_firewall_module`, `make_firewall_segment` |

## Tests

```bash
uv run --package pyvelo-intent pytest packages/pyvelo-intent/tests
uv run basedpyright packages/pyvelo-intent
uv run ruff check packages/pyvelo-intent
```
