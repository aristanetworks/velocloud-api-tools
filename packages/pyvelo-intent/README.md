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
            state, module="firewall", patch={"firewall_logging_enabled": True}
        )
        preview = await pipeline.save(state, dry_run=True)
        preview.raise_for_errors()
        print(preview.plan.render())


asyncio.run(main())
```

The preview is the plan of the save:

```text
edge 101, enterprise 1, configuration 10
firewall: update
  + firewall_logging_enabled: true
```

Its first line names what would be written to. A module the save would create
shows as `firewall: insert`; `preview.modules_inserted` and
`preview.modules_updated` hold the names. "What a save writes" explains the
lines.

`dry_run=True` skips write API calls; loading still reads live configuration.
Calling `save(state)` sends configuration writes, and the state then continues
from what the VCO stored (see "After a save"). A save that is not a dry run
raises `SaveValidationError` when a write-time validator reports an error, and
sends nothing. A dry run returns the errors in `SaveResult.validation_errors`,
with what it would write, and `raise_for_errors()` raises them.
`SaveResult.dry_run` says which kind of save a result is from, and
`SaveResult.written` holds the writes a save stored, one `ModuleWrite` each:
empty for a dry run and for a save with nothing to change.
`build_default_pipeline` registers the edge and profile name resolvers. Each
state resolves the names in its patches with resolvers of its own
(`state.resolvers`), loaded for its enterprise when the state is loaded, so one
pipeline can serve several enterprises: `load` and `load_profile` give the new
state an unloaded copy of every resolver registered on the pipeline, add a
segment resolver, and load it.
Validators are registered explicitly: pass a `ValidatorRegistry` as
`validator_registry=` to `ConfigPipeline` or `build_default_pipeline`, or
register on `pipeline.validators`.

Application, address-group and service-group names need more resolvers. For
application names, register `ApplicationResolver(client)` (from
`pyvelo.config.resolvers.application`) with `pipeline.resolvers.register(...)`
before loading; each state's copy is then loaded with the state. For the other
types, load a resolver with `state.resolver_context` and register it on the
state (`state.resolvers.register(resolver)`), or give numeric IDs in patches. A
state you build yourself, from a cache for example, has no resolvers until
`await pipeline.load_resolvers(state)`. Without its resolver, a name in a patch
raises `PatchError` at that field, unless the field's ID can be text, when the
name is taken as the ID. An ID is always accepted.

## The patch format

`apply` takes a module, a patch and, optionally, an address. The address is
shorthand for nesting: `segment="Global Segment", path="outbound", patch=P` is
`patch={"segments": {"Global Segment": {"outbound": P}}}`. `path` is field
names joined by dots, each a field that holds one sub-model, except the last,
which may be any field; it does not step into list items, which the patch
names. `segment` names a segment like any other selector of the `segments`
list: by name, in any case, or by `segmentId`. Without an address, `patch`
must be a `dict`.

`apply` changes `state` in place and returns that same object: `candidate =
await pipeline.apply(state, ...)` gives no second state to fall back on
(`candidate is state`). To try a patch and drop it, load the state again.
`apply` is a coroutine: without `await` it applies nothing.

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
- A patch that cannot be applied raises `PatchError` (a `ValueError`). Its
  `path` is where in the patch it failed, its `message` the reason, and its
  `kind` one of the twelve values below, for a caller that reacts to the
  cause. Resolver errors keep their types (`ResolutionError` for an unknown
  name). A patch that applies but that a
  save could not write raises `LossyFieldError` (see "What a save writes"),
  and a patch to a module a save does not write (control plane) raises
  `PatchError`. In every case the state, and `plan()`, are as they were.

| `kind` | The patch... |
|---|---|
| `not_json` | holds a value that is not JSON, a number that is not finite, or nests too deep |
| `address` | was sent, by the `module`, `segment` or `path` of the call, to a place that takes no patch |
| `unknown_field` | has a key that names no field |
| `duplicate` | names one thing twice: a field by two keys, an item by two selectors, a key by two adds or by two items of a list value |
| `null` | has `null` where it means nothing: a field that cannot be unset, or an item's value |
| `invalid_value` | has a value, an item or a changed model that validation refuses, or a name that cannot be looked up |
| `operation` | has an item operation that is malformed, or that this list or map does not take |
| `selector` | has a selector or anchor that is not one for this list |
| `no_item` | has a selector or anchor that names no item |
| `several_items` | has a selector or anchor that names more than one item |
| `exists` | adds, or renames to, a key, alt key or value that is already there |
| `segment_rule` | breaks a rule of the `segments` lists (below) |

Every list has a kind, which says how its items are named:

| Kind | Declared by | Items are named by |
|---|---|---|
| keyed | `KeyField(...)` | the key, alt key, label or position |
| value | being `list[str]` or `list[int]`, without `Positional()` | the value, or a position |
| positional | `Positional()`, or a list of models that declares nothing | a position |
| untyped | being `list[Any]` or `list[dict]` | not at all: give a list value |
| map | being `dict[K, Model]` | the map key; no order, so no positions or moves |

A keyed list names its items by a *key*: one field of the item (a rule's
`name`, a VLAN's `vlan_id`) or several together (a static route's destination,
prefix, gateway and interface). Some keyed lists also declare an *alt key*, a
second field that identifies an item on its own, usually an ID the VCO issues
(a rule's `ruleLogicalId`, a segment's `segmentId`), and some a *label*, a name
people use for the item that is not kept unique (a WAN link's `name`; its key
is its `internalId` with its address family).

An object on a list is item operations, keyed by selector. A selector is a
key, alt key, label or value in string form (`"10"` names VLAN 10), `at(n)`
(the item at position `n` of the list as it was), or `"+"` for a new item of a
keyed or positional list (in a list of values and in a map, `add` goes under
the new value or key itself). Under it, a plain object changes the item (and
may rename it to a key no other item has), `add(body, ...)` adds one,
`delete()` removes it, and `move(...)` moves it. An empty object changes
nothing and is accepted for an item that exists, so the dump of an item whose
fields are all defaults can be written back. A place is one of `first=True`,
`last=True`, `before=selector` or `after=selector`; an `add` without one goes
last. A selector that names no item, or more than one, raises and lists the
matches; `add` of a key or value already in the list raises. An alt key is
kept unique the same way: an `add` or a change that gives an item an alt key
another item has (a rule's logical ID, a segment's `segmentId`) raises, and so
does a list value that repeats one. Every selector is resolved against the
list as it was, then changes, deletes, and adds and moves (in the order
written) are applied.

`list_kind` (`pyvelo.core`) reports a sixth value, `"undeclared"`, for a list
of models that declares neither `KeyField` nor `Positional()`. A patch treats
such a list as positional; the separate value marks a list whose kind has not
been decided.

A list whose key has several parts, such as the static routes, names an item
by the parts joined by commas: `10.0.0.0,24,192.168.1.1,GE3`. A part that
holds a comma or a double quote is written as a JSON string instead
(`"a,b",24,"192.168.1.1",GE3`), and in Python a tuple of the parts works too.
An `add` under the comma form stores each part as text: on a field declared
`str | int` (a static route's `cidr_prefix`), `10.2.0.0,24,192.168.1.1,GE3`
stores `'24'`, while the quoted form stores `24`. Use the quoted form, or give
the field in the body, when the type of the value matters. A text that reads as
two keys of the list, one as the quoted form (where `true` is a boolean, so
`a,24,true,GE3` names the item `a,24,True,GE3`) and one as plain parts split at
the commas, is refused with both items named, and the quoted form of each key
to choose with. A selector that is not a key of such a list is refused with the
key of one of its items as the example.

Where a text could be read in more than one way, the first of these wins: an
item's key, a position (`"#2"`, which is what `at(2)` returns), `"+"`, an alt
key, a label. So an item whose alt key or label reads like a position, or like
another item's key, cannot be named by it. `item_selector(model, field_name,
position)` (`pyvelo.core`) gives the selector that names one item and no
other. It tries the item's label, then its key, then its alt key, then `"#n"`,
and takes the first that `select_items` resolves to that item alone: a
candidate that names another item, several items, or is refused as ambiguous
(the two readings of a composite key above) is passed over, and the result is
None where none is left. `select_items(model, field_name, selector)` goes the
other way and gives the positions a selector names. Both take a map of models
too, where the selector is the map key and the position counts the entries in
the map's order. `key_selector(key)` is the text of a key alone:
`10.0.0.0,24,192.168.1.1,GE3` for a static route.

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
# value: SNMP allowed IPs (on a profile: an edge does not write this section)
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

The SNMP patch is for a state loaded with `load_profile`. On an edge it raises
`LossyFieldError`, because this library writes the SNMP section for a profile
only.

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
  change of one the edge layer lacks first adds its placeholder entry (an
  empty object adds none); `add` of an inherited segment raises (write a plain
  object), and so does `delete` of one the edge layer has no entry for. A
  place (`move(...)`, or `"_position"` on a change) is for the edge's own
  segments, the ones its module had when it was read. A save writes the
  segments the edge only inherits after those, in the profile's order, so
  `apply` refuses a place for one of them, and a place given `before` or
  `after` one of them. Once a save has written an entry for such a segment, it
  is one of the edge's own.
- On a profile, `apply` refuses a patch that adds or removes a segment, or
  changes a segment's descriptor (the `segment` object: its name, `segmentId`,
  type and logical ID), in whatever form: `add` or `delete` under a selector, a
  list value for `segments` that leaves a segment out or brings a new one, or
  an object that rewrites the descriptor. A segment belongs to all three of
  device settings, QoS and firewall, and adding one through a patch is not
  supported yet. Changing what a segment holds, moving it, and a list value
  that keeps every descriptor are fine.

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
  + segments["Global Segment"].bgp.neighbors["10.0.0.2"] (first): {...}
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

Beside its `data`, a device settings or QoS module has `refs`: the rows that
tie the module to the enterprise objects it uses (segments, address groups,
services). The library derives them from the data, so you edit the data and
never the refs. Setting a provider adds the row it goes with and clearing it
removes the row; `state.plan().render()` lists each as `+ refs <name>
(<segment>): <value>` or `- refs <name> (<segment>): <value>`, without the
segment for a row of the module, and a module that sends its refs with no row
changed says `refs: sent as read`. A device settings or QoS write with segments
always carries its refs, because the VCO's processors run on every such write.
What disagrees in the refs as read is a `warning: <message>` line, and the
write leaves it as it is.

This is `state.plan().render()` for the firewall patch of "The patch format",
with `firewall_enabled` set as well, on an edge whose profile has a segment the
edge module lacks:

```text
edge 101, enterprise 1, configuration 10
firewall: update
  ~ segments["Global Segment"].outbound[allow-web].match.dport_high: 443 -> 8443
  - segments["Global Segment"].outbound[old-rule]
  + segments["Global Segment"].outbound[block-ssh] (after guest-wifi): {"action": {"allow_or_deny": "deny"}, "comments": null, "loggingEnabled": false,…
  ~ segments["Global Segment"].outbound: order: guest-wifi first
  ~ firewall_enabled: false -> true
  reconciled segments: Guest
```

- The module `version` from the read is sent. If the module changed on the VCO
  since, the save raises an error that lists your lost edits (see "After a
  save").
- A save asks the VCO for each module it writes, as stored, and the state
  continues from it, so no reload is needed. Rule IDs the VCO assigns are in
  the state after the save, and `state.plan()` right after a save is empty.
  The state takes more edits and saves.
- A new firewall rule has no `rule_logical_id` until the save, when the VCO
  issues one; the field is the VCO's, and it may issue another on a later
  write. A new QoS rule gets its `rule_logical_id` from this library when the
  patch builds it, a random UUID, and it is sent as it is.
- `state.plan().render()` shows each module's changes against the raw data as
  read, as in the example above. A `+` line is a key or an item that is added,
  a `-` line one that is removed, and a `~` line a value that changes. An item
  added to a keyed list has its place after its path, `(first)` or
  `(after other)`. A list whose items change order has an `order:` line that
  names the items that move, each with its new place: five of them, then a
  count. A value is shown as JSON, cut to 80 characters, and the two values of
  a `~` line are cut where they first differ. `SaveResult.plan` is the same
  plan, and `dry_run=True` reports it without writing.
- A state loaded with `load` saves the edge's own layer. `state.profile` is
  there to read: an edit made through it is not saved, and the next `plan()`,
  `get_changed_modules()` or `save()` raises `LayerNotSavedError` (from
  `pyvelo_intent`) with the fields, once, before anything is sent. To change
  the profile, load it with `load_profile`.
- When the profile has segments the edge module lacks, a write to that module
  adds them as entries that inherit everything, after the edge's own entries
  and in the profile's order. The plan lists them under "reconciled segments".
  Looking one up (`state.edge.firewall.segment("Voice")`) or editing it does
  not change where it is written, and a lookup alone writes nothing. `apply`
  refuses a `move` of such a segment (see "The patch format"). An edit made by
  assignment that moves such an entry, such as reordering the model's
  `segments` list in place, changes its place in the state only.
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
- An edit that a save could not write raises `LossyFieldError`: setting a
  field to its default where the module as read has no key for it (on an edge,
  a field the edge inherits), and an edit to a section this library does not
  write at that level. `apply` raises it and leaves the state as it was. An
  edit made by assigning to the models is found later: `plan()`,
  `get_changed_modules()` and `save()` raise it, before anything is sent, until
  the assignment is changed to a value that can be written or the state is
  loaded again. The message names the fields. `LossyFieldError` is exported by
  `pyvelo_intent` (and by `pyvelo`, `pyvelo.client` and
  `pyvelo.client.exceptions`).
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
- A patch that moves items (a `move`) plans each item's change at the item, and
  one more change at the list: its order entry, which names the items that
  move, each with its new place. An item added to a keyed list has its place in
  its own change. In a keyed list the items' unknown keys are still kept.
- When an added item would get the name of an item that was read (a new rule
  named as a rule the same plan renames, or an item without a key at the
  position of an item that is named by position), the two cannot be told apart
  by name, and the list plans as one change.
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
  firewall rule's `ruleLogicalId`) and the removal of a default it adds back.
  Each has the value read (`as_read`), the value sent (`intended`) and the value
  stored. Saving again would not store them either, so they are not to be
  redone. After a refusal such a change is a lost edit instead, when it is
  still as read: see below. A ref row is never left out: see
  `RefsDroppedError`, below.

A write that is not known to be stored as planned raises one of three errors,
from `pyvelo.client.exceptions` and re-exported by `pyvelo_intent`.
`ConfigConflictError` says the VCO refused the write because the module changed
since it was read, or seems to have (`stated`, below, says which);
`WriteNotAppliedError` says the VCO stored the write otherwise than as sent;
`RefsDroppedError`, a `WriteNotAppliedError`, says that the changes not stored
are all ref rows (below).
`WriteOutcomeUnknownError` says the write was sent (or, for an insert sent
again, that an earlier save may have sent it) and the module as stored could
not be read. When the read itself fails, its own error is raised, and the
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
| anything else: some of your changes are stored and some are not, a value you edited is stored as neither the one read nor the one sent, or the VCO counted the write and the module read back does not hold it | `WriteNotAppliedError`, or `RefsDroppedError` when every change not stored is a ref row |

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

A write that carries refs (a device settings or QoS update with segments
always does) reads the module's `version` first, one more request. If the
version is the one read, the save goes on. If it changed, the save reads the
module as the VCO holds it now and checks it against your changes. When it
already holds them (a retry whose first copy was stored), the save passes and
the state continues from that module. Otherwise the save raises
`ConfigConflictError` with `error.sent` False and `error.stated` False, and
nothing was sent: `error.module` is the module as it is now, with your edits in
`lost_edits` to redo on it. A module its configuration no longer holds raises it
too, with `error.module` None: read the configuration again.

Which of the two a stale write raises depends on the VCO's conditional check
of the version. With it on, the VCO refuses the write, and the save raises
`ConfigConflictError`. With it off, the VCO does not store the data and
answers with the module as it is. The save then raises `ConfigConflictError`
too, when none of your changes is found stored and each value is as read. It
raises `WriteNotAppliedError` in three cases: another writer also changed a
value you edited (that edit is marked as a clash); another writer made one of
your edits too, so that it counts as stored, while another of your edits is
not; or the write carried refs and the VCO replaced them while it left the
data as it was, so that your refs changes are under `applied` and your data
changes under `lost_edits`. Reading the version before a write that carries
refs prevents the last, unless the module changes between that read and the
write.

A VCO with the check off says nothing when it answers with the module, so the
save tells a stale write by what is stored. Where no change of a write can be
found stored (it changes only fields the VCO owns, or removes only defaults
the VCO adds back), the save does not ask for the module. The VCO then answers
with a row count, which is zero for a stale version, and the save reads the
module back. Such a write costs one more request.

What a save cannot tell, on a VCO with the check off: a stale write whose other
changes are all stored already, by another writer or by an earlier save of
yours whose answer was lost, passes. A left-out change beside them is then
reported in `left_out` although the write that carried it was not stored. Look
at `left_out` when a save follows one that failed.

A ref row the VCO does not store is not a stored write either. The VCO
replaces a module's refs row by row, and it skips a row whose object it cannot
resolve (a service that is not there) and stores the others. The save compares
the refs it gets back with the ones it sent, row by row by identity and never
by content. `RefsDroppedError` is raised when every change that is not stored
is a ref row: a row you added that the VCO did not store, or a row you removed
that it kept. An update the VCO answered with the module needs one change that
is stored beside, so a ref change beside a data edit: one whose only changes
are ref rows, none of them stored, is a `ConfigConflictError`, and its message
names both causes: the module changed since it was read, or the VCO would not
take a new row because it cannot resolve its object. The error lists each lost
row by its ref name and identity. To redo one you added, check that the object
exists and set the field again on `error.module`, which reads it as unset; for
one you removed that the VCO kept, clear the field again, as it reads as set.
The message gives the patch for each. The VCO writes nothing of its own to put
a skipped row back. When it took none of the rows the write sent,
`error.every_row_lost` is true and `error.refs_as_read` holds the refs as the
module was read, to put back by sending
`configuration/updateConfigurationModule` with
`{"id": <module ID>, "_update": {"refs": <error.refs_as_read>}}`
(`update_configuration_module`). A row you did not change that the VCO leaves
out of the refs it returns is not an error: it is a `server_changes` entry, as
is any difference in the rows of the refs the VCO's own processors manage.

`ConfigConflictError` and `WriteNotAppliedError` carry:

| Attribute | Holds |
|---|---|
| `lost_edits` | the planned changes that are not stored: each with its `path`, the value `as_read`, the value `intended` and the value `stored`; `clash` is true where the stored value is not the one read, because something else changed it too. An added item has its `place`. The order of a keyed list is one entry (`is_order`): its three values are the items' names in order, and its `moves` are the moves that put the stored order right |
| `applied` | the planned changes that are stored |
| `left_out` | the planned changes the VCO stores its own way, as in `SaveResult.left_out` |
| `server_changes` | what else the VCO stores otherwise than sent, as in `SaveResult.server_changes` |
| `module` | the module as the VCO holds it now |
| `plan` | the module's `ModulePlan` |
| `refs_as_read` | on `RefsDroppedError`: the module's refs as read, to put back when `every_row_lost` is true |
| `stated` | on `ConfigConflictError`: whether the VCO said it refused the version, or the save inferred it |
| `completed` | the writes of the same save before this one, which are stored |
| `redo_patch(include_clashes=False)` | the patch that redoes the lost edits on `module`, for `apply(state, module=error.module_name, patch=...)` |
| `not_redone(include_clashes=False)` | the lost edits that patch leaves out |

A planned change counts as stored when the VCO holds the value you meant,
whoever put it there: an edit that another writer made identically is under
`applied`, not under `lost_edits`.

The entries are `PlannedChange` and `ServerChange`, which `pyvelo_intent`
exports (they are defined in `pyvelo.core.response_check`). The message lists
them by path. A path uses the VCO's names for fields.
`change.python_path(type(data))`, with `data` the module's data
(`state.module("firewall").data`), gives the same path in the names a patch is
written in. Here, a change to a rule's port and a new rule are
refused, and another writer had changed the same port and another key:

```text
Configuration module 11 (firewall) changed since it was read (version v1): the VCO refused the version
lost edits (not stored; `error.redo_patch()` redoes those it can, `error.not_redone()` lists the rest):
  segments["Global Segment"].outbound[block-ssh] (first): read (nothing), meant {"action": {"allow_or_deny": "deny"}, "comments": null, "loggingEnabled": false,…, stored (nothing)
  segments["Global Segment"].outbound[allow-web].match.dport_high: read 443, meant 8080, stored 8443  [clash: also changed on the VCO]
stored otherwise than sent:
  syslog_forwarding: read false, stored true
```

The message shows at most 20 entries of each list and says how many more there
are; `error.render(limit=None)` has them all.

The order of a keyed list is checked as a value is. A `move` that is not stored
is a lost edit at the list. So is the place of an added item: when the VCO
stores the item in another place than planned, the item is under `applied` and
the order under `lost_edits`. An order the VCO changed where your plan placed
nothing is a server change. No list is known that the VCO puts in an order of
its own. A save that places an item in such a list would raise every time; the
fix is a pattern under `own_order` in the table of rewrites.

After either of those two errors the state holds the module as stored, so your
edits to that module are no longer in it. `error.redo_patch()` is the patch that
redoes them on the module as stored. Apply it and save again; no reload is
needed:

```python
except (ConfigConflictError, WriteNotAppliedError) as error:
    state = await pipeline.apply(
        state, module=error.module_name, patch=error.redo_patch()
    )
    print(state.plan().render())  # review it, then save again
```

The patch puts an added item back in its place, as an `add`, and makes a lost
order again, and it names each item as the stored list names it. An item you
added is added again, never set on an item another writer put in its place. The
patch leaves out a clash, because redoing one overwrites what another writer
stored there; `error.redo_patch(include_clashes=True)` redoes those too.
`error.not_redone()` lists the lost edits the patch leaves out: the clashes, a
lost ref row (a patch changes data, not refs), and an edit that no patch can
make or whose item is no longer stored or cannot be named. An edit at a key the
VCO does not know, which a write drops, is neither redone nor listed. Check it
before you save. In a rule list the first match decides, so check the order with
`state.plan().render()` too. `apply` can still refuse the patch, changing
nothing, where the stored data no longer takes it (a rename to a name another
writer gave an item, for example), and where the lost edit was made by assigning
to the models and is one `apply` refuses as a patch (a segment added to a
profile). Make such an edit again the way you made it.

The modules written before the failing one are stored and in the state
(`error.completed`). Any other error of a request (a transport error, for
example) leaves that module and the later ones with your edits; a second save
sends what still differs. For every error a save raises once it has started to
write, a cancellation included, `save_progress(error)` (from `pyvelo_intent`)
gives a `SaveProgress`: the writes stored before it (`completed`), the module
whose write raised (`failed`, None when the error came after a write was
stored) and the modules not sent (`pending`). The progress is attached to any
error raised inside the write loop, one raised while the first request is built
included, so `completed` is empty and `failed` names that module. It is None
for an error raised before the first write is attempted (planning, validation),
and for any error that did not come from a save. `save_progress` also follows
`__cause__`, else `__context__`: after `asyncio.timeout` it finds the progress
on the cancellation behind the `TimeoutError`, and an error raised while you
handle an earlier save's error can return that save's progress.

`WriteOutcomeUnknownError` has no lists and no module. `error.answer` says what the VCO said of the write itself: `"refused"` (it refused the version, so this write stored nothing), `"counted"` (it counted the write, so the changes are most likely stored) or `"row"` (its answer could not be read as this module). `error.detail` says why the module could not be read, and `error.plan` holds what was sent (what would have been, for an insert sent again). The state keeps the module as read, with your edits. So there is nothing to redo: its `redo_patch()` is `{}` and its `not_redone()` is empty. For an update, saving again is safe: if the first write was stored, the VCO refuses the old version, the save reads the module and reports what is stored. For an insert, saving again is safe too: the state has recorded it, and the next save reads the configuration before it inserts (below).

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
save. A device settings segment's `vrrp` is not one of the library's models
and has no such guard: an assignment to `vrrp.root` does not raise, and is
reported by the next `plan()` or save like an in-place edit. Take the object
from the state again and redo the edit. To reuse a detached object as a new
item, put a deep copy of it in the state (`copy.deepcopy(rule)`): the copy is
not detached.

A write whose answer never arrived (a transport error after the request was
sent, a cancelled save) may or may not be stored, and so may one that raised
`WriteOutcomeUnknownError`. For an update the next save settles it without
writing twice: it sends the same version, which the VCO refuses or ignores when
the first write was stored, and the save goes by the module it then holds. It
passes when that module has every one of your changes, and otherwise raises one
of the errors above with the module as stored: after a refusal the VCO states,
for instance, a change to a value it stores its own way is a lost edit. An
insert carries no version, so the state remembers it
(`state.unconfirmed_inserts`), and the next save first reads the configuration.
A module of that name found there is taken as the insert's answer and checked
against the plan like any write, and nothing is inserted again; with none
there, the save inserts. A `dry_run` does not read: it plans the insert and
adds a warning with the code `insert_unconfirmed`.

Known limits:

- A plain list or dict taken from a module before a save is checked for
  in-place edits only while the model it belongs to is alive: the object whose
  field it is, or one that contains that object. For `rules = seg.outbound`
  that is `seg`, or the firewall data it came from. An item of the list does
  not count: holding `rules[0]` does not keep `rules` checked. Keep the model
  the list came from, or take the list from the state again.
- A lookup through a detached layer that adds a placeholder
  (`fw.segment("Guest")` on a `fw` taken before the save) is reported by the
  next `plan()`. Take the layer from the state again.
- A save that changes a profile's device settings and its QoS (or firewall)
  together may need a second save, when the device settings change propagates:
  the later module's write raises with your edits in `lost_edits`, and the
  state holds that module as stored, so apply `error.redo_patch()` and save
  again. A save that does this itself is planned and not built.
- On a VCO with the conditional check off, a stale write that carries refs may
  replace them while the data is not stored, overwriting another writer's
  refs; the save then reports your data changes, if any, as not stored. The
  save reads the module's version before it sends refs, which narrows this to
  a change between that read and the write; a module with no `version` is
  written without the read.
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

## Scope and data handling

- Updates support firewall, device settings, QoS, and WAN modules. A missing
  edge firewall or QoS module can be created; it starts empty and is inserted
  on save. Control plane modules can be read. A save does not write them:
  `apply` refuses a patch to one (`PatchError`), and a state whose control
  plane module was edited by assignment cannot be saved or dry-run
  (`TypeError`, before anything is sent) until it is loaded again.
- A failed `apply` changes nothing. Patches and saves run one after another,
  though: a failure leaves earlier patches, and the remote writes of a save
  that stopped part way, applied; there is no transaction or rollback.
- One state may be used from several tasks of one event loop. `apply` and
  `save` hold `state.lock` for the whole call, so they run one after the other:
  an `apply` made while a save is writing waits and is then applied to the
  state as stored, and a second `save` writes only what still differs. An edit
  made by assigning to the models while a save is writing is not waited for.
  The save stores the module without it, puts the module as stored in the
  state, and raises `DetachedObjectError` with the paths of such edits: redo
  them. A path that is a placeholder a lookup added (`segment(name)`, for a
  segment the edge only inherits) is no edit and needs no redo. The save stops
  at that module: the error's note names the modules written, and the next
  save writes the rest. To make such an edit safely, hold the lock around it
  (`async with state.lock:`). `apply` and `save` take the lock themselves:
  called inside that block by the same task they raise `RuntimeError`, so call
  them after it. A task that holds the lock and waits, inside the block, for a
  child task that calls `apply` or `save` waits without end: nothing detects
  it.
- The `override_consistency` validator is a traversal placeholder and currently
  reports no violations. Other registered validators cover specific structural
  or domain checks; they do not guarantee that every patch is valid for a live VCO.
  A validator reports on the whole state, so an error in a module the save does
  not write blocks the save too; the path of the error names the module.
- `ConfigState`, the module objects, field changes (`get_field_changes`),
  validation messages, and the change objects of a plan, of a `SaveResult` and
  of a write error (`before`, `after`, `as_read`, `intended`, `stored`) hold
  complete configuration values, including addresses and configuration
  secrets. So does a patch from `redo_patch()`. Redact them before logging,
  sharing, or committing exports. This library does not persist configuration
  automatically.
- Three kinds of text leave secrets out. `render()` of a plan and the message
  of a write error (`str(error)`) show `"***"` in place of the value of every
  field the models mark as a secret (`Secret`, in `pyvelo.core`: passwords,
  pre-shared keys, SNMP communities), and in place of a ref row. `repr()` of a
  plan or of a write holds no values, and `repr()` of a `SaveResult` holds
  neither module data nor the changes it reports, only their number per module
  (it still holds the validators' messages). A response that fails to parse
  raises pydantic's `ValidationError` without the input in its text;
  `error.errors()` still has it. Nothing else is hidden. A secret is printed
  as it is in two places: under a key the models do not know, which includes
  everything inside a field typed `dict[str, Any]` (`EdgeZscaler.deployment`,
  a WAN link's `private_network`), and in the data of a module whose data is
  not a model (a generic module: `analyticsSettings`, `atpMetadata`).
- Keep API tokens and environment files outside version control. TLS verification
  uses the SDK default. Tests use synthetic fixtures and do not require a live VCO.

## Surface

| Module | What it provides |
|---|---|
| `pyvelo_intent` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, `ConfigPlan`, the types a save hands back (`ModulePlan` in `ConfigPlan.modules`, `ModuleWrite` in `SaveResult.written`, `ConfigFieldChange` from `get_field_changes()`), the entries a save reports (`PlannedChange`, `ServerChange`), `SaveProgress` and `save_progress`, the patch helpers (`add`, `delete`, `move`, `at`), `PatchError` and `PatchErrorKind` (the type of its `kind`), diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), the intent error types, the write errors `ModuleWriteError` (the base of the next three), `ConfigConflictError`, `WriteNotAppliedError`, `WriteOutcomeUnknownError` and `DetachedObjectError` (from `pyvelo.client.exceptions`), `LossyFieldError` and `ResolutionError`, helpers (`build_default_pipeline`, `confirm_save`, `load_config`) |
| `pyvelo_intent.config.pipeline` | `ConfigPipeline` (load/load_profile → apply → save), `build_default_pipeline` |
| `pyvelo_intent.config.state` | `ConfigState`, `StateLock`, `SaveResult`, `ConfigPlan`, `SaveProgress`, `save_progress`, `ConfigFieldChange`, `ValidationWarning` |
| `pyvelo_intent.config.helpers` | `load_config` (a YAML file validated against a model; `ConfigInputError` for a file it cannot use, a key given twice included), `confirm_save` (a yes or no prompt, which prints the plan it is given: `confirm_save(plan=preview.plan)`) |
| `pyvelo_intent.config.validators.*` | Structural built-ins (`key_uniqueness`, `override_consistency`, `required_fields`) + domain validators (firewall, device settings) |
| `pyvelo_intent.config.(firewall\|device_settings\|qos\|wan\|control_plane)` | Per-module configuration helpers |
| `pyvelo_intent.diff` | Generic keyed-list diff engine: `diff_intents`, `DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`. Each side must have each key once (`ValueError` otherwise). |
| `pyvelo_intent.errors` | `IntentError`, `ConfigInputError`, `LayerNotSavedError`, `SaveValidationError`, `UnknownRegistryEntryError` |
| `pyvelo_intent.testing` | Test fixtures: `MockVcoClient`, which holds a `ModuleStore` (`pyvelo.testing`) as `store` that serves an edge's configuration stack, module reads and module writes, so a second `load` sees what a save stored (`MockVcoClient.for_state(state)` seeds it from a state), `make_firewall_module`, `make_firewall_segment` |

## Tests

```bash
uv run --package pyvelo-intent pytest packages/pyvelo-intent/tests
uv run basedpyright packages/pyvelo-intent
uv run ruff check packages/pyvelo-intent
```
