# Changing configuration with pyvelo

`pyvelo.config` is the configuration part of [`pyvelo`](README.md). It provides
the `ConfigPipeline` (load → snapshot → apply → diff → save), a generic keyed-list
diff engine, validators, and helpers for scripting against the VCO
configuration API.

Requires Python **3.13 or newer**. Licensed under Apache-2.0.

## Install

From the workspace root:

```bash
uv sync --all-packages
```

As a Git dependency from the public repository:

```toml
[project]
dependencies = ["pyvelo"]

[tool.uv.sources]
pyvelo = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo" }
```

For reproducible deployments, set the Git source to a reviewed commit
using `rev = "<commit-sha>"`. This installation route does not require a PyPI release.

## Preview a configuration change

Set `VCO_HOST` to your Orchestrator hostname and `VCO_TOKEN` to an API token
with the appropriate enterprise permissions. 

```python
import asyncio

from pyvelo.client import VcoClient, get_vco_credentials
from pyvelo.config.validation import ValidatorRegistry
from pyvelo.config import ConfigPipeline
from pyvelo.config.validators import key_uniqueness, required_fields


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

`dry_run=True` skips write API calls; loading still reads live configuration,
and a dry run of a plan with hub role steps also reads the hub services (see
"Hubs").
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

The network-service resolver is registered on each state but not loaded by
`load` or `load_profile`, which therefore cost no more requests than before. It
is loaded when a plan or a patch first needs it (see "What a save writes"), or
by `await pipeline.load_resolvers(state, NetworkServiceIdentity)`, which loads
only that resolver. The call takes any identity type: it loads the state's
resolver for it, or first registers an unloaded copy of the pipeline's (for the
network services, a new resolver if the pipeline has none), and raises
`ValueError` when neither has one.

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
(`candidate is state`). `apply` is a coroutine: without `await` it applies
nothing.

To take a module's edits back, `await pipeline.revert(state,
module="firewall")` puts the module back as the VCO last gave it (as loaded,
or as the last save or write error left it): every edit to it since is gone,
not the last patch alone, however it was made, and a module the state created
is removed. No other module is touched and nothing is cascaded: the next
`plan()` judges what is left, so an edit elsewhere that relied on this module
is refused there (`SegmentSetError`, `DanglingReferenceError`). Objects taken
from the state before are detached, as after a save, and nothing is sent.
`revert` raises `ValueError` for a new module whose insert may have reached the
VCO after a failed save (save again instead) and for a state built without
`take_snapshot()`.

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
- A hub is added to a `vpn` list (`edgeToEdgeHub.vpnHubs`,
  `edgeToEdgeDetail.vpnHubs`, `backHaulEdges`) or to a QoS rule's
  `backhaulEdges` by its name, `{"+": add({"name": "Hub-A"})}`, or by its
  logical ID, `{"+": add({"logicalId": "<logical ID>"})}` (in a rule,
  `edgeLogicalId`): the other fields of the entry come from the Edge or the hub
  cluster. A hub added to a rule must be saved first: see "Hubs".
- A patch that cannot be applied raises `PatchError` (a `ValueError`). Its
  `path` is where in the patch it failed, its `message` the reason, and its
  `kind` one of the thirteen values below, for a caller that reacts to the
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
| `fixed_list` | edits a fixed part of a list whose entries, keys and order are fixed (the QoS `defaults` of a segment): adds, deletes, moves, renames or reorders an entry, or changes a field but `action` |

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
from pyvelo.config import add, at, delete, move

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
  object), and so does `delete` of one the edge layer has no entry for. An
  `add` of a segment the profile does not have raises too: an edge uses only
  its profile's segments, so the entry would be saved and never used. Assign
  the segment on the profile first, then change it on the edge. These rules
  hold for an `add` under `"+"` that gives only an ID, where the ID names a
  segment, as for one that gives `segment.name`. The ID is read as
  `merge_patch` reads it (`"7"` and `7.0` are the `segmentId` 7). With a
  segment resolver registered, the ID names the segment the resolver has
  loaded under it, and an ID none of its loaded segments has raises
  `ResolutionError`. A state from `load` has its segment resolver loaded; a
  resolver that must be loaded first (`requires_load`) is loaded by `apply`,
  but the stock `SegmentResolver` is not one, and unloaded it knows no
  segment. With no segment resolver, the `segmentId` of a segment the edge
  only inherits gets the inherited-segment refusal; the `segmentId` of one
  of the edge layer's own segments meets the place rules, and the add then
  raises `PatchError` (`invalid_value`) for the missing `segment.name`, as
  does any other add with no name. A
  place (`move(...)`, or `"_position"` on a change) is for the edge's own
  segments, the ones its module had when it was read. A save writes the
  segments the edge only inherits after those, in the profile's order, so
  `apply` refuses a place for one of them, and a place given `before` or
  `after` one of them. Once a save has written an entry for such a segment, it
  is one of the edge's own.
- On a profile, a segment belongs to all three of device settings, QoS and
  firewall, and a patch assigns it from any of them. An `add` of an enterprise
  segment under `segments` of any of the three assigns it as `assign_segment`
  does (see "Assigning a segment to a profile"): each of the three modules that
  lacks the segment gets the UI's template entry, and the body of the add is
  merged into the entry of the module the patch is for, as a change (a
  `_position` moves it). A client sends one `add` per module, in any order,
  each with the body it wants, and needs no call of its own to assign:
  `{"segments": {"Guest": {"_op": "add", "rules": [...]}}}` to QoS, then
  `{"segments": {"Guest": {"_op": "add", "vpn": {...}}}}` to device settings.
  The segment is named by the selector in any case, or under `"+"` by
  `segment.name` of the body (an add under `"+"` without a name raises
  `segment_rule`), as the enterprise has it: an unknown name raises
  `ResolutionError` with suggestions, a state with no segment resolver raises
  `PatchError` (`invalid_value`), a body whose `segment` gives another
  `segmentId`, type or logical ID raises `segment_rule`, and so does an add on
  a profile that lacks one of the three modules. A list value for `segments`
  assigns the same way: an item for a segment the module had no entry for is
  merged into the template entry, and the other items replace the module's as
  given. An `add` of a segment the module already had as read raises `exists`:
  change it with a plain object. The segment is judged per module, as read, so
  the second and third adds of a segment, after the first assigned it, are
  accepted, and an add to a module that lacks a segment the others have
  completes the profile. "As read" means as loaded, or as last saved: a second
  `add` of the same segment to the same module before a save is merged into
  the entry again, not refused.
  A patch does not remove a segment: a `delete`, or a list value that leaves a
  segment out, raises `segment_rule`, because a segment is removed from all
  three modules or none. The one exception is an orphan, a QoS or firewall
  segment that the profile's device settings lacked as read, which is removed
  as the segment-set rule of `plan()` allows. Changing what a segment holds
  and moving it are fine, and so is a list value that keeps every descriptor;
  a rewritten descriptor (the `segment` object: its name, `segmentId`, type
  and logical ID) raises `segment_rule`. The assignments are tried on copies of
  the three modules first, so an error leaves all three as they were.
- The `defaults` of a QoS segment are a fixed list: only the `action` of an
  entry can change, by `{"defaults": {"Default-Any-UDP": {"action": {...}}}}`.
  A list value for `defaults` is accepted when it keeps the keys, their order
  and every field outside `action`, and an item that gives no `ruleLogicalId`
  keeps the current one, so an entry read, changed in its actions and sent
  back is a valid list value; one that gives another `ruleLogicalId` is
  refused (`fixed_list`), and the message says to leave it out. The entry an
  assignment creates is the exception: in the body of an assigning `add` and
  in a new item of a list value, a `defaults` item takes the `ruleLogicalId`
  the assignment gave it, whatever the body says, so an entry copied from
  another profile is accepted without repeating that profile's rule IDs in
  the enterprise. Its keys, their order and its other fixed fields must still
  be the template's, or the patch raises `fixed_list`.

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

The firewall's module-level values (`firewall_enabled`, `syslog_forwarding`,
`services` and the rest) have no flag: the edge stores a key or inherits it.
A patch on a value the edge inherits starts from the profile's value, or from
the display default (the packaged profile template's) where the profile stores
none, and the whole value becomes the edge's own and shows in `plan()`.
`{"services": {"ssh": {"enabled": true}}}` stores the whole `services` object,
with `ssh.enabled` true and the rest of what the model holds as the profile has
it (no `icmp`, which the model does not hold), and the plan shows one added
key. A patch on a value the edge already stores merges into it, a scalar sets
it, and `null` makes the edge inherit again (the plan shows the key removed):
`{"syslog_forwarding": null}`. The keys of a group (`stateful`,
`network_protection`, `atp`) change owner together, so
`{"stateful_firewall_enabled": false}` on an edge that inherits the group adds
its three keys, with the settings as the profile has them. A patch that raises
leaves the group as it was. `state.effective` shows each value an edge uses and
where it comes from; see "The effective view".

The QoS module works the same way. Its two module-level values
(`serviceRateLimit`, `qoe`) are stored by the edge or
inherited, and a segment's `cosMapping` is the edge's own when it is stored
with `override` true. A patch on one the edge inherits starts from the
profile's value (or the display default) and stores it whole:
`{"serviceRateLimit": {"enabled": false}}` stores the profile's rate limit with
`enabled` false, and `{"serviceRateLimit": null}` makes the edge inherit again.
`{"segments": {"Voice": {"cosMapping": {"realtime": {"high": {"value": 40}}}}}}`
on an edge that has no `Voice` entry makes the entry from the profile's
segment, with the profile's `cosMapping`, `override` true and that one value
changed; the plan lists the `cosMapping` as your change. The flag is never
patched: a patch naming `override` is refused, and `{"cosMapping": null}`
inherits. A segment's `defaults` belong to the profile: a patch of `defaults`
on an edge is refused (the VCO would add an edge's to the profile's); patch
the profile's state.

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
The row of a hub added to or removed from a Branch-to-Hub list is the one
exception: it points at the hub's service, which exists only once the role
steps have run, so the plan shows `refs: sent as read` and the save adds or
removes the row for the write that follows the steps (see "Hubs").
What disagrees in the refs as read is a `warning: <message>` line, and the
write leaves it as it is. `ConfigPlan.warnings` holds the same warnings as
data, each with its module's name before its path, and `save` and `dry_run`
copy them to `SaveResult.warnings`.
An edge module that stores an object the VCO uses although its flag says it
inherits (a segment's `cosMapping` with `override` false: the composer takes a
present edge object whatever its flag) has a `flag_off_in_use` warning, at the
object's path, in the same list and in `render()`, also when nothing else in
the module changes; its message gives the patch that inherits
(`{"segments": {"Voice": {"cosMapping": null}}}`) and the one that keeps the
object as stored with its flag turned on (`{"segments": {"Voice":
{"cosMapping": {}}}}`; `localize()` in Python). A patch that names values
inside such an object changes only those values and turns the flag on, so
the edge keeps running with the object `state.effective` showed as the edge's.

A DNS or authentication provider is set by assignment, with the service's
logical ID (`provider.service = uuid.UUID(...)`), or by `apply`, with the
logical ID or the service's name (`patch={"dns": {"primaryProvider":
{"service": "dns-a"}}}`). The row needs the enterprise's network services, and
`load` does not read them: the first `apply`, `save` or `dry_run` that needs
them reads the table, once for the state, and goes on. A name no service has
raises `ResolutionError` and leaves the state as it was, and so does a service
of the wrong type for the field (`dns` for a DNS provider, `authentication` for
the authentication provider, `cloudSecurityService` for a CSS provider): the
message names the service and both types and says to give a service of the
wanted type. `state.plan()` does not read it and raises `ResolverNotLoadedError`
for a provider set before the table is loaded;
`await pipeline.load_resolvers(state, NetworkServiceIdentity)` (from
`pyvelo.core.identity`) reads it at a time of your choosing.
`get_field_changes()` lists a provider that is set, replaced or cleared as one
change at the provider's field
(`segments["Global Segment"].dns.primary_provider.service`), with the service's
logical ID before and after, or `None`. When the provider's stub had no `ref`,
setting a provider also writes the stub's `ref` into the data, and that is its
own change in the list (`...dns.primary_provider.ref`), before the provider's.
A provider edit on the layer a state
does not save (the profile layer of an edge state) raises `LayerNotSavedError`
like any other edit there, and one made while its module is being written
raises `DetachedObjectError` after the write.

A QoS rule's backhaul hub adds a `deviceSettings:backHaulEdge` row the same
way, on a profile and on an edge. The row points at the hub's service for the
profile and segment, which the profile's device settings rows hold, so a hub
that has no service there raises `HubServiceNotFoundError`: the hub is saved
first and the rule after (see "Hubs").

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
- The plan lists `deviceSettings` first, then the other modules in the order the
  configuration lists them, for an edge as for a profile, and a save writes
  them in that order.
- A profile's device settings, QoS and firewall modules hold the same
  segments, so `plan()` compares the three by `segmentId`. An edit that removes
  a segment from one raises `SegmentSetError` (from `pyvelo.config`), except
  for a QoS or firewall entry whose segment device settings did not have when
  they were read. An edit that adds a segment to one or two of the three
  raises it too, naming the segments and the modules that lack them, with the
  patch `{"segments": {"Guest": {"_op": "add"}}}` and the call
  `pipeline.assign_segment(state, "Guest")` that complete it. Modules that
  already disagreed when read, with nothing added, are left as they are: each
  such segment is a warning with the code `segments_disagree`, at
  `segments["Guest"]`, in `ConfigPlan.segment_warnings` (also in
  `ConfigPlan.warnings` and `SaveResult.warnings`), and `render()` ends with a
  `warning: <message>` line for it. A profile that lacks one of the three
  modules has nothing to compare.
- A value that names a segment (a LAN network's or an interface's `segmentId`)
  or a backhaul hub (a QoS rule's backhaul entry) must name one the
  configuration has: a segment of the device settings `segments`, a hub in the
  `vpn.backHaulEdges` of the rule's segment. `plan()` checks every module of
  the target, on every save and with `validate=False` too, and raises
  `DanglingReferenceError` (from `pyvelo.config`), naming each value, its
  module and its path, for a value the edit adds, or one whose segment or hub
  the edit removes. An edge's values are checked against its profile's device
  settings as read as well as its own. `apply` accepts an edit that leaves such
  a value, so that two edits that are only right together go in either order;
  `plan()`, `save` and a dry run refuse the state until it is mended. For a
  hub, `error.mending` holds the patches that mend it, as `MendingPatch`
  objects (from `pyvelo.config`) with seven fields: `kind`, `does` (one line
  that says what the patch does), `target_type`, `module`, `patch`, `segment`
  and `path`. A `remove_reference` patch takes the hub out of the rule that
  names it; an `add_member` patch makes it a backhaul hub of the rule's
  segment. The `remove_reference` patches come first, and the patches of one
  kind together mend every hub reference of the error: send those of one kind.
  `module`, `patch`, `segment` and `path` are `apply`'s arguments
  (`await pipeline.apply(state, module=m.module, patch=m.patch,
  segment=m.segment, path=m.path)`), `target_type` says which configuration
  the patch is for (`"edge"` or `"profile"`), and `dataclasses.asdict(m)` is a
  JSON object; the message gives each patch as `send <JSON> to <module>`. On
  an edge, the `add_member` patch is for the profile's device settings. Or
  take the edits back with `pipeline.revert(state, module=...)`. A value that
  named nothing as read, at the same path, is left as it is: a
  `reference_kept` warning in `ConfigPlan.check_warnings` (also in
  `ConfigPlan.warnings` and `SaveResult.warnings`), and a
  `warning: <message>` line in `render()`. `client.update_module` writes one
  module and does not run the check.
- A hub added to or removed from a profile segment's hub, bridge or backhaul
  list has role steps (`ConfigPlan.steps`), which `render()` shows first and a
  save sends before it writes the modules, and a hub row that the device
  settings write carries. "Hubs", below, has all of it.
- A segment added to a profile module has an `added segments: Guest` line, and
  `get_field_changes()` lists it as one change at `segments["Guest"]`, from
  `MISSING` to the entry as written. A QoS segment that lacks template
  `defaults` has them added to the module's write when the module is written
  (`repaired defaults: Guest: Default-Any-UDP, Default-Any-Other`); the plan
  warns with the code `defaults_repaired` either way, and `missing defaults`
  is the same line for entries it leaves out. A repaired default's
  `allowConditionalBh` is the segment's `vpn.conditionalBackhaul` in the
  profile's device settings as it is then. `save(state, repair_defaults=False)`
  and `state.plan(repair_defaults=False)` leave them out.
- A state loaded with `load` saves the edge's own layer. `state.profile` is
  there to read: an edit made through it is not saved, and the next `plan()`,
  `get_changed_modules()` or `save()` raises `LayerNotSavedError` (from
  `pyvelo.config`) with the fields, once, before anything is sent. To change
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
- A missing edge firewall or QoS module starts empty. It is inserted once it
  differs from that empty layer: patches that cancel out leave nothing to
  insert. The insert holds the segment entries the save reconciles and what the
  patches made local: a patch on a firewall or QoS value the edge would inherit
  copies that value from the profile (or the display default) into the insert,
  as it does for a module the edge has (see "The patch format"); no other
  profile value is copied.
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
  `pyvelo.config` (and by `pyvelo`, `pyvelo.client` and
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

## The effective view

`state.effective.firewall` and `state.effective.qos` are what an edge uses,
computed on the client from the edge layer and the profile layer it inherits
from. Each value is a `Resolved`
with the value itself, its `origin` and its `source`:

| Origin | The value is |
|---|---|
| `Origin.EDGE` | the edge's own: it stores the key (or, for a `presence_wins` object, the object) |
| `Origin.PROFILE` | the profile's, because the edge inherits it |
| `Origin.FACTORY` | a display default, because neither layer stores one; it is never written |

`source` is where the value sits in the layer it came from (for a rule,
`segments[1].outbound[0]`). The view is read-only, is computed again on each
read so it follows your edits, and creates nothing: an edge with no row for a
module has the profile's view. A value the profile has turned off for every
edge (`firewall_enabled: false`) is shown as the profile's, whatever the edge
stores.

```python
fw = state.effective.firewall
fw.syslog_forwarding.origin  # Origin.EDGE, Origin.PROFILE or Origin.FACTORY
fw.syslog_forwarding.value  # the value the edge uses

state.effective.qos.service_rate_limit.origin  # the same, for QoS
```

The view is the client's reading of rules the VCO applies on its own, so
`pipeline.compare_effective(state)` checks it against the VCO's answer. It reads
the modules the library has converted (`modules=None`, which is the firewall
and QoS; name one with `modules=["QOS"]`, and another module raises
`ValueError`) with
`edge/getEdgeConfigurationModules` for the edge the state was loaded for, and
compares each with `state.effective`, by wire path and by value only, since the
VCO's answer has no origins. Before it compares, it reads the state's profile
configuration again, so a profile that changed since the load is not a finding;
the state itself is not changed. It raises `ValueError` for a profile state and
for a state with changes that are not saved (those differ from the VCO by
design; save, or load the edge again).

```python
comparison = await pipeline.compare_effective(state)
print(comparison.render())  # by module, then path
assert not comparison.findings
```

Each difference is an `EffectiveDifference` (module, path, the two values,
the view's origin and source, a category, and a reason where there is one).
There are six categories, and three of them are findings:

| Category | Meaning | A finding? |
|---|---|---|
| `differs` | both sides have a value and they differ; a display default the server contradicts is one | yes |
| `client_only` | the view gives an edge or profile value the server's answer lacks | yes |
| `server_only` | the server's answer has a value the view lacks | yes |
| `factory` | the view gives a display default where the server has no value, or the same one | no, information |
| `ignored` | the path is on the ignore list, is a secret, or is a segment the composer left out; reported with its reason | no |
| `unconverted` | the path is not converted yet (the firewall's `inbound`, `inboundV6` and `services.icmp`, a segment's `firewall_logging_enabled` and `etm`, a QoS segment's `webProxy` and a QoS module's `qosClientConnector`), so the view has no value to compare | no, information |

`comparison.findings` is the entries that are findings, and `entries` all of
them. A key is reported once, at the highest path that is absent from a side.
Under a display default, a key the server lacks is `factory` (information),
and a value it has that differs is `differs`. A list the view builds itself
(a segment's rules, or `segments`) that is empty equals an absent one; a stored
empty list the server lacks is `client_only`. Segments are matched by
`segmentId` in any order (the view puts the profile's first and the composer
the edge's), and the rules of a segment, edge entries then profile entries, by
position. For QoS the segments' `rules` are compared the same way, and a
segment's `cosMapping` is the edge's stored object even when its `override` flag
is off, because that is the object the composer takes; the module's
`serviceRateLimit` and `qoe` and a segment's `cosMapping`
are display defaults (`factory`) where neither layer stores one and the server
has none.

The VCO's composer keeps only the segments in use on the edge (an enabled
network with an interface, an enabled routed interface or subinterface, a
loopback, and the Global Segment) in its device settings, QoS and firewall. A
segment the view has and the composed module lacks is `ignored` with that
reason; one the composed module has and the view lacks is `server_only`. The
composed read also returns `null` for a module it could not compose, which it
does without saying why: that is `ComposedModuleError` (its `module` is the
module's name), not a finding.

The call does not ask the VCO for encrypted keys (`encryptKeys`), which a VCO
may refuse. A secret is never compared: a path that holds one is `ignored` with
that reason, and its value is shown as `***`.

When a rule of the view names a security service group
(`securityServiceGroup`), the composer adds three keys to it that neither layer
stores: `urlCategoryFiltering`, `urlReputationFiltering` and `atp_action`. They
are confirmed, not ignored. `compare_effective` reads the enterprise's security
objects (`enterprise/getSecurityObjects` for `securityServiceGroup`,
`urlCategoryFiltering`, `urlReputationFiltering` and `idps`: four reads, with
the state's enterprise ID) only when a rule of the firewall names a group (QoS
rules are not searched), and expects:

- `urlCategoryFiltering` and `urlReputationFiltering`: the `data` of the object
  of that type the group names (its `data` holds `{"id": ...}` for each);
- `atp_action`: `atp_enabled` true, and `ids_enabled`, `ips_enabled` and
  `atp_logging_enabled` from the `idps` object the group names (`idsEnabled`,
  `ipsEnabled` and `logEnabled`).

A group that names no object of a type expects no key for it. The expected
values are compared like the rule's own keys, with the rule's origin and a
source of its `securityServiceGroup`: a match makes no entry, a different value
is `differs`, an expected key the server lacks is `client_only`, and a key on a
rule that names no group is `server_only`. When the group, or an object it
names, is not among the enterprise's security objects, nothing can be expected
for the keys that depend on it, so the entries at the rule's security keys say
so ("the rule's security service group, or an object it names, is not among the
enterprise's security objects"): they are still findings, which a person can
resolve by creating the object or fixing the rule's group. The composer's
output for a `maliciousIpFiltering` object has not been seen, so none is
expected: a key the composer adds for one is `server_only`.

`pyvelo.config.effective.EFFECTIVE_IGNORES` is the list of known
divergences, each an `EffectiveIgnore` with its module, a path (whole parts,
`*` for one part, `[*]` for one list item; it covers everything below it) and
the reason. It is empty: an entry is added only after a divergence is
understood, so that a divergence is visible in the comparison and never hidden.
A finding you meet is either a wrong rule in the library, which gets fixed, or
a divergence you have understood, which gets an entry with its reason.

## Assigning a segment to a profile

A profile holds each of its segments in three modules: device settings, QoS and
firewall. `pipeline.assign_segment(state, "Guest")` adds the entry the
Orchestrator UI makes for a new segment to each of them that lacks it, in
memory, and the next save writes them, device settings first, with its
`deviceSettings:segment` row:

```python
state = await pipeline.load_profile("Branch profile", enterprise_id=1)
state = await pipeline.assign_segment(state, "Guest")
print(state.plan().render())  # added segments: Guest, in each of the three
result = await pipeline.save(state)
```

- A patch assigns the segment too, so a client that sends every change as a
  patch has no case of its own for it. An `add` under `segments` of any of the
  three modules assigns the segment to all three, then merges its body into
  the entry of the module it is for; the next add, to another module, merges
  into the entry that is there. In any order:

  ```python
  await pipeline.apply(
      state, module="qos", patch={"segments": {"Guest": add({"rules": [rule]})}}
  )
  await pipeline.apply(
      state,
      module="device_settings",
      segment="Guest",
      patch=add({"vpn": {"conditionalBackhaul": True}}),
  )
  await pipeline.apply(state, module="firewall", patch={"segments": {"Guest": add()}})
  ```

  Each is the entry of `assign_segment` with that body merged into it. The
  rules of a patch to the `segments` of a profile are in "The `segments` lists
  have rules of their own", above.
- The name is resolved as in a patch: the enterprise's segment in any case, and
  `ResolutionError` with suggestions for one it does not have. A state with no
  segment resolver raises `LookupError`; `await pipeline.load_resolvers(state)`
  gives it one (`load` and `load_profile` load it).
- The entry of each module is the template of the UI, with the segment's
  descriptor. Every QoS rule of a new entry, in `rules` and in `defaults`, has a
  `ruleLogicalId` made when the segment is assigned, so `dry_run`, `save` and
  a second `plan()` send the same IDs. A firewall rule has none until the VCO
  issues it. An entry nobody edits is no change from the template: the plan has
  an `added segments: Guest` line, and an edit made before the save (`apply`
  of `{"segments": {"Guest": {"rules": {"Box": {...}}}}}`, or assignment to the
  models) is a change from it.
- `qos_rules` are the rules of the new QoS entry in place of the template's:
  each a `QosRule` or its JSON, in order, with a missing `ruleLogicalId` made.
  `qos_rules=[]` gives an entry with no rules. Two rules of one name or one
  `ruleLogicalId` raise `ValueError`, and so do `qos_rules` for a profile whose
  QoS module has the segment already: they would be dropped, so change those
  rules with a patch.
- A profile that has the segment in one or two of the modules is completed:
  only the modules that lack it get an entry. A QoS entry that is there and
  lacks some of the template's four `defaults` gets them, as a write of the
  module would repair them, with `allowConditionalBh` set to the segment's
  `vpn.conditionalBackhaul` in the device settings. A segment that is in all
  three, with its defaults complete, raises `ValueError`: change it with a
  patch.
- An edge target, a profile that lacks one of the three modules, and a module
  that is not at the profile level (`load_profile` loads them so; call
  `.for_profile()` on a profile you built) raise `ValueError`.
- All or nothing: the assignment is first planned on copies of the modules, and
  an error leaves the state and `plan()` as they were. The call holds
  `state.lock`, as `apply` does.
- The save writes the three modules, one request each, device settings first,
  with the new `deviceSettings:segment` row and every other row as read, then
  QoS, then firewall. Writing device settings can change the other two, so each
  is read for its version and rebased when it changed (see "After a save"): your
  edits go onto the module as it is then, together with the entry of the
  assignment and the template `defaults` the write repairs, with the IDs they
  were planned with. A module that holds a segment this save adds by then
  (another writer assigned it) raises `ConfigConflictError`, with `error.sent`
  False, which names every such segment, and sends nothing for that module.
- A write that fails after device settings is stored leaves the profile with
  the segment in device settings and not in the module that failed, and
  `plan()` raises `SegmentSetError`, which names the segment and the modules
  that lack it. For a write that lost the segment, a `ConfigConflictError` (the
  usual case: the VCO answered with the module and nothing of the write stored)
  or a `WriteNotAppliedError`, `error.redo_patch()` is the assignment again,
  with the edits made to the entry inside it
  (`{"segments": {"Guest": {"_op": "add", ...}}}`), and
  `apply(state, module=error.module_name, patch=error.redo_patch())` makes it,
  so that the next `save` writes the modules that are left. Template
  `defaults` that a write repaired and the VCO did not store are neither
  redone nor listed in `error.not_redone()`: the next write of the module
  repairs them again. After a `WriteOutcomeUnknownError`, or an error of the
  request, the modules keep their entries, and saving again completes the
  assignment.

## Hubs

A hub is an Edge or a hub cluster that the Edges of a profile connect to. A
profile's device settings list the hubs of each segment in three lists, one for
each role a hub plays there:

| Role | List (under the segment's `vpn`) | In Python |
|---|---|---|
| `hub` (Branch-to-Hub) | `edgeToEdgeHub.vpnHubs` | `hub_edges.vpn_hubs` |
| `bridge` (Edge to Edge through the hub) | `edgeToEdgeDetail.vpnHubs` | `edge_to_edge_detail.vpn_hubs` |
| `backhaul` | `backHaulEdges` | `backhaul_edges` |

An entry is `{"name", "type", "logicalId"}`, with `type` `edgeHub` for an Edge
and `edgeHubCluster` for a cluster. A list is keyed by `logicalId`, with the
name as its label, so a plan names an entry by logical ID, a patch selects one
by either, and a reorder is no change. A hub is in a list only if its network
service has the role, and a role is set by a role call to the VCO, which a
write of the module does not make: so a save of a profile makes the calls an
edit needs, then writes the module.

- **Adding and removing.** A hub is added to a list by its name or its logical
  ID and removed by either, through `apply`, as JSON or with the helpers:

  ```python
  state = await pipeline.load_profile("Branch profile", enterprise_id=1)
  await pipeline.apply(
      state,
      module="device_settings",
      patch={
          "segments": {
              "Global Segment": {
                  "vpn": {
                      "enabled": True,
                      "edgeToEdgeHub": {
                          "enabled": True,
                          "vpnHubs": {"+": add({"name": "Hub-A"})},
                      },
                      "backHaulEdges": {"+": add({"logicalId": "<logical ID>"})},
                  }
              }
          }
      },
  )
  # removing: {"Hub-A": delete()} in a list, {"Hub-A": {"_op": "delete"}} as JSON
  ```

  The same edit as JSON is `{"segments": {"Global Segment": {"vpn":
  {"edgeToEdgeHub": {"vpnHubs": {"+": {"_op": "add", "name": "Hub-A"}}}}}}}`.
  The resolver reads the enterprise's Edges and clusters on first use
  (`HubIdentity`, once per state: the Edge list is paged at 200, so a VCO with
  15,000 Edges takes about 75 requests, plus one for the clusters and one for
  their members). A name that an Edge and a cluster share needs the logical ID,
  and a member of a cluster is no hub of its own: name the cluster. The hubs
  must exist: a hub, an Edge or a cluster is made in the Orchestrator, not
  here.
- **The steps.** `plan()` derives one role call per hub, list and segment that
  changed, from the module as read and as edited, and holds them in
  `ConfigPlan.steps` (`HubStep`: `action`, `role`, `hub`, `segment`). The order
  is the UI's: disable backhaul, disable bridge, disable hub, enable hub, enable
  backhaul, enable bridge; within one kind, the segments in the order of the
  module's `segments`, then the hubs in the order of their list. A hub removed
  from a segment's hub list gets no backhaul or bridge disable there: disabling
  the hub clears its roles. `render()` shows them first, after the target line:

  ```text
  profile "Branch profile", configuration 100
  steps:
    disable hub for Hub-A on segment Global Segment
    enable hub for Hub-B on segment Global Segment
    enable backhaul for Hub-B on segment Global Segment
  deviceSettings: update
    - segments["Global Segment"].vpn.edgeToEdgeHub.vpnHubs[a1a1a1a1-...]
    + segments["Global Segment"].vpn.edgeToEdgeHub.vpnHubs[b2b2b2b2-...] (first): {...
  ```

  A plan with steps is for a profile; an edge has none. An added hub is looked
  up by logical ID, so `plan()` raises `ResolverNotLoadedError` for one before
  the hubs are loaded, which `apply` of a name and a save both do
  (`await pipeline.load_resolvers(state, HubIdentity)` does it now). A dry run
  reads the hub services (two `getEnterpriseServices` requests, the Edge hubs
  and the clusters) to say which steps would be sent, and nothing else beyond
  loading the hub resolver.
- **The checks.** `plan()` checks a segment whose `vpn` an edit changes against
  the UI's rules for its lists, before any call. A save that breaks one raises
  `HubListError` (from `pyvelo.config`), which names the segment, the lists and
  the hubs, and gives the patch that mends it. The rules: the Branch-to-Hub
  list is non-empty if and only if `edgeToEdgeHub.enabled` (so the patch that
  adds the first hub to a segment must also set it, and `vpn.enabled`, as
  above: the library sets neither; and turning either off needs the hubs
  removed first); no hub is listed twice in one list;
  with `edgeToEdge` on and `useCloudGateway` off the bridge list is not empty;
  a hub in the backhaul or bridge list is in the Branch-to-Hub list too (so a
  hub that leaves that list leaves the others); a segment with hubs has
  `vpn.enabled` on; and an added hub is an Edge or cluster of the enterprise,
  no cluster member, whose `type` and name (compared without regard to case)
  are those its logical ID names. The warnings, in `ConfigPlan.check_warnings`
  (also in `ConfigPlan.warnings` and `SaveResult.warnings`, and as `warning:`
  lines in `render()`): `hub_list_as_read` for a rule that is broken the same
  way as read (the save leaves it), `hub_roles_disagree` for a hub whose roles
  as read do not match the lists it is in, `reference_kept` for a value that
  names nothing as read and still does not, and `hub_in_use` (below). One module
  alone cannot change these lists: `client.update_module` and `plan_module`
  raise `HubListError` for a payload that does.
- **The save.** A save with steps does this, in order. It reads the version of
  the profile's device settings module (another writer's change stops the save
  here with `ConfigConflictError`, and nothing is sent; the state then holds
  the module as stored, as after a refused write, and the edits it lost are in
  `error.lost_edits`; a module that already holds the planned changes, from a
  write whose answer was lost, counts as written). It reads the hub services
  (two requests) and decides every step from them, which steps are in place and
  the ID each call takes, so that a refusal (a hub whose service the VCO holds
  twice is a `HubListError`) comes before the first call. It sends each step
  the VCO does not already have in place, reads the services again, and writes
  the device settings with the hub rows the steps made, without reading the
  version a second time. The write is the module as read with your edits and
  `deviceSettings:vpn:edgeHub` (`edgeHubCluster` for a cluster) rows: a hub
  added to the Branch-to-Hub list gets one, whose object is its service
  (`+ refs deviceSettings:vpn:edgeHub (Global Segment): <service ID>` in
  `ModuleWrite.plan`), and a hub removed from it loses its row. A row of a hub
  that stays is never removed, and a hub the VCO has no service for after its
  enable raises `HubServiceNotFoundError`. `SaveResult.steps` has a
  `StepOutcome` for each step: `"sent"`, `"in_place"` (the VCO already had the
  roles, so nothing was sent), or, for `dry_run=True`, `"would_send"` or
  `"in_place"`.
- **When a step fails.** A request that fails once a step may have been sent is
  raised as `SaveStepError` (from `pyvelo.config`), from the VCO's error: `step`
  is the step whose call failed, or `None` when a module write failed
  (`module_name` names it), `steps_done` and `steps_in_place` list the others,
  and `written` the modules stored; `save_progress(error)` gives the modules
  not written. A failure before any step was sent (the version read, the
  services read, a write when every step was in place) propagates as it is, and
  so does a cancellation. Nothing a step did is undone and the state keeps its
  edits: a second `save(state)` plans the steps again, finds the ones sent in
  place, sends the rest and writes the modules.
- **The removal window.** Once a hub's disable has run, its service, its row
  and the refs that point at it are gone, so if the module write then fails the
  stored profile still lists the hub while the VCO drops it for every Edge,
  until a second `save` finishes. The Orchestrator UI has the same window.
- **A hub that rules use.** An Edge hub removed from a Branch-to-Hub list that
  edge QoS rules use loses its service to the role call, and with it the
  backhaul refs of those rules, which keep the hub in their data. The services
  read says so (it asks for `edgeQOSUsage` only for such a removal): with
  `save(state, on_hub_in_use="warn")`, the default, the save adds a `hub_in_use`
  warning per hub and segment to `SaveResult.warnings`, naming the Edges, and
  goes on; with `on_hub_in_use="refuse"` it raises `HubListError` (`edges` holds
  their IDs and names) before any step is sent. A dry run does the same.
- **A hub in a QoS rule.** A rule's `edge2EdgeRouteAction.routeCfg.backhaulEdges`
  names its hubs, by name or `edgeLogicalId`, on a profile and on an edge. `apply`
  gives the entry its `logicalId`, the hub's service for the profile and the
  rule's segment, and the save adds the `deviceSettings:backHaulEdge` row that
  points at it. A hub that is not saved there has no service: it is saved first
  and the rule after, in two saves, and `apply` raises `HubServiceNotFoundError`
  for a hub added to the lists in the same unsaved edit, with the patch that
  adds it to both lists. An entry built by assignment without its `logicalId` raises the same
  error at `plan()`. The rule's hub must be in the segment's `backHaulEdges`,
  and a value that names a segment or a hub the configuration does not have is a
  `DanglingReferenceError` (see "What a save writes"). Taking a hub that a rule
  of the profile still names out of `backHaulEdges`, and out of the rule, are
  two edits that go in either order: `plan()` refuses the state until both
  are made, and the error's `mending` holds the patch for each way to mend it.
- **Not supported.** Creating a hub, an Edge or a cluster, or a cluster's
  members; and role calls from an edge (an edge state has no steps: its QoS
  rules name the hubs of its profile).

## After a save

Each module a save writes is replaced in the state by the module as the VCO
stored it. `SaveResult` also reports, per module, what the VCO did beside
storing your changes. Neither list is an error.

- `server_changes` is what the VCO stored otherwise than sent, at paths that
  are not lost edits. It lists keys the VCO added, IDs it issued or revised (a
  firewall rule's `ruleLogicalId`, a WAN link's `internalId`), values it keeps
  in its own form (an IPv6 address in canonical form), the default it added
  back where you removed one, and what another writer changed beside your
  edits. A value stored as it was read is not listed here. An entry with
  `merged` true was found before the write, in a module written after a
  profile's device settings, and your edits were put on it (below).
- `left_out` is the planned changes the VCO stores its own way, which are
  neither applied nor lost. They are a change to a field the VCO owns (a
  firewall rule's `ruleLogicalId`) and the removal of a default it adds back.
  Each has the value read (`as_read`), the value sent (`intended`) and the value
  stored. Saving again would not store them either, so they are not to be
  redone. After a refusal such a change is a lost edit instead, when it is
  still as read: see below. A ref row is never left out: see
  `RefsDroppedError`, below.

A write that is not known to be stored as planned raises one of three errors,
from `pyvelo.client.exceptions` and re-exported by `pyvelo.config`.
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
A save with role steps makes this read before its first step instead
(`client.check_module_version`), so that a module another writer changed stops
it with nothing sent, and the device settings write that follows does not read
again.

A save of a profile that writes its device settings rebases the modules it
writes after them. Writing device settings can change the profile's other
modules: it rewrites QoS rule flags, and it updates the versions of all of them
when a segment becomes in use. So each module written after the device settings
reads its `version` first, refs or none, one more request per module. If the
version is the one read, the save goes on. If it changed, the save reads the
module in full and puts your edits on it, so a second save is not needed. The
write is sent with the new version, and what changed beside your edits is in
`SaveResult.server_changes` with `merged` true. It raises `ConfigConflictError`
(`error.sent` False, `error.stated` False, nothing sent) only where the change
reached a path you edited too, or a list you edit an item of had items added or
removed; `error.lost_edits` lists your edits, to redo on `error.module`. When
the module already holds every one of your changes, nothing is sent for it. A
ref row your edit adds or removes (a QoS rule's backhaul hub) is derived again
against the refs read, so a row already there is not added twice, and the hub's
service is found in the device settings as that save stored them. The
modules written before the device settings, and every module of an edge save,
are not rebased: a changed version raises as above. The cost is one version
read per module written after the device settings, and one full read of the
module when its version changed.

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
The message gives the patch for each, and `error.redo_patch()` gives it with
the real value. The VCO writes nothing of its own to put a skipped row
back. When it took none of the rows the write sent, `error.every_row_lost` is
true and `error.refs_as_read` holds the module's refs as read, less the rows of
`deviceSettings:vnfs:edge`, `objectGroup:addressGroup` and
`objectGroup:portGroup`, which the VCO keeps itself and would store twice if
they were sent back. Put the rows back by sending
`configuration/updateConfigurationModule` with
`{"id": <module ID>, "_update": {"data": <the module's data as stored now>,
"refs": <error.refs_as_read>}}` (`update_configuration_module`). If you speak
only JSON, read the data as stored now with `configuration/getConfiguration`
and `{"id": <configuration ID>, "with": ["modules"]}`, and send as `refs` the
refs from the read the save was planned on, less those three names (the VCO's
own refs now lack the rows it dropped). A row you did not change that the VCO leaves
out of the refs it returns is not an error: it is a `server_changes` entry, as
is any difference in the rows of the refs the VCO's own processors manage.

`ConfigConflictError` and `WriteNotAppliedError` carry:

| Attribute | Holds |
|---|---|
| `lost_edits` | the planned changes that are not stored: each with its `path`, the value `as_read`, the value `intended` and the value `stored`; `clash` is true where the stored value is not the one read, because something else changed it too. An added item has its `place`. The order of a keyed list is one entry (`is_order`): its three values are the items' names in order, and its `moves` are the moves that put the stored order right |
| `applied` | the planned changes that are stored |
| `left_out` | the planned changes the VCO stores its own way, as in `SaveResult.left_out` |
| `server_changes` | what else the VCO stores otherwise than sent, as in `SaveResult.server_changes` |
| `module` | the module as the VCO holds it now; None on a `ConfigConflictError` for a module gone from its configuration, where nothing was sent and the state keeps the module as read |
| `plan` | the module's `ModulePlan` |
| `refs_as_read` | on `RefsDroppedError`: the module's refs as read, less the three append-only names, to put back when `every_row_lost` is true |
| `stated` | on `ConfigConflictError`: whether the VCO said it refused the version, or the save inferred it |
| `sent` | on `ConfigConflictError`: False when nothing was sent, because the version read before a write found the module changed (or gone): a write that carries refs, or one after a profile's device settings, where a path you edited was also changed |
| `completed` | the writes of the same save before this one, which are stored |
| `redo_patch(include_clashes=False)` | the patch that redoes the lost edits on `module`, for `apply(state, module=error.module_name, patch=...)` |
| `not_redone(include_clashes=False)` | the lost edits that patch leaves out |

A planned change counts as stored when the VCO holds the value you meant,
whoever put it there: an edit that another writer made identically is under
`applied`, not under `lost_edits`.

The entries are `PlannedChange` and `ServerChange`, which `pyvelo.config`
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
A lost DNS or authentication provider (a ref row of a field that stands for
it) is redone by setting the field again, in the segment as the stored list
names it: `{"segments": {"Global Segment": {"dns": {"primaryProvider":
{"service": "<logical ID>"}}}}}`, or `null` for a provider you cleared.
`error.not_redone()` lists the lost edits the patch leaves out: the clashes, a
lost ref row of any other kind (a patch changes data, not refs), a provider
whose segment is no longer stored or cannot be told from another by its
identity (the segment is found by its ID, never by its name), and an edit that
no patch can make or whose item is no longer stored or cannot be named. An edit
at a key the VCO does not know, which a write drops, is neither redone nor
listed. Check it before you save. In a rule list the first match decides, so check the order with
`state.plan().render()` too. `apply` can still refuse the patch, changing
nothing, where the stored data no longer takes it (a rename to a name another
writer gave an item, for example), and where the lost edit was made by assigning
to the models and is one `apply` refuses as a patch (a segment added to a
profile). Make such an edit again the way you made it.

The modules written before the failing one are stored and in the state
(`error.completed`). Any other error of a request (a transport error, for
example) leaves that module and the later ones with your edits; a second save
sends what still differs. For every error a save raises once it has started to
write, a cancellation included, `save_progress(error)` (from `pyvelo.config`)
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

Objects taken from the state before a save or a `revert`
(`seg = state.edge.firewall.segment("Global Segment")`) belong to the modules
the save or the `revert` replaced, and are detached. Reading one is fine. An assignment to one
raises `DetachedObjectError`, and an in-place edit to a list or dict it holds
(`seg.outbound.insert(0, rule)`) is reported, once, by the next `plan()` or
save. A device settings segment's `vrrp` is not one of the library's models
and has no such guard: an assignment to `vrrp.root` does not raise, and is
reported by the next `plan()` or save like an in-place edit. Take the object
from the state again and redo the edit. To reuse a detached object as a new
item, put a deep copy of it in the state (`copy.deepcopy(rule)`): the copy is
not detached. A detached object put back as it is makes its module refuse
every patch (`merge_patch`, `apply`, `assign_segment`) with
`DetachedObjectError`, which names where it sits (`paths`) and changes
nothing, also for a patch that would not reach it.

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
- A profile's segment is assigned with `assign_segment` or an `add`, but not
  removed: an edit that removes a segment the device settings module has
  raises `SegmentSetError` (a patch: `PatchError`), and only an orphan, a QoS
  or firewall segment that the device settings lack, can be removed.
  Inserting a missing module into a profile is not supported either.
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
  pre-shared keys, SNMP communities, a WAN network's ISP key), and in place of a ref row. `repr()` of a
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
| `pyvelo.config` | Re-exports the public surface: `ConfigPipeline`, `ConfigState`, `SaveResult`, `ConfigPlan`, the types a save hands back (`ModulePlan` in `ConfigPlan.modules`, `ModuleWrite` in `SaveResult.written`, `ConfigFieldChange` from `get_field_changes()`), the entries a save reports (`PlannedChange`, `ServerChange`), `SaveProgress` and `save_progress`, `StepOutcome` (in `SaveResult.steps`) and `HubStep` (in `ConfigPlan.steps`), the patch helpers (`add`, `delete`, `move`, `at`), `PatchError` and `PatchErrorKind` (the type of its `kind`), `MendingPatch` (a patch that mends an error, as data: `kind`, `does`, `target_type`, and the `module`, `patch`, `segment` and `path` of the `apply` call that sends it), diff types (`DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`), the intent error types, the write errors `ModuleWriteError` (the base of the next three), `ConfigConflictError`, `WriteNotAppliedError`, `WriteOutcomeUnknownError` and `DetachedObjectError` (from `pyvelo.client.exceptions`), `GroupOwnershipError` (an edit through one member of a coupled group), `ComposedModuleError` (`compare_effective` found a module the VCO composed as `null`), the comparison types `EffectiveComparison`, `EffectiveDifference` and `EffectiveCategory`, `LossyFieldError` and `ResolutionError`, helpers (`build_default_pipeline`, `confirm_save`, `load_config`) |
| `pyvelo.config.pipeline` | `ConfigPipeline` (load/load_profile → apply → save, and revert), `build_default_pipeline` |
| `pyvelo.config.state` | `ConfigState`, `StateLock`, `SaveResult`, `StepOutcome`, `ConfigPlan`, `SaveProgress`, `save_progress`, `ConfigFieldChange`, `ValidationWarning` |
| `pyvelo.config.helpers` | `load_config` (a YAML file validated against a model; `ConfigInputError` for a file it cannot use, a key given twice included), `confirm_save` (a yes or no prompt, which prints the plan it is given: `confirm_save(plan=preview.plan)`) |
| `pyvelo.config.validators.*` | Structural built-ins (`key_uniqueness`, `override_consistency`, `required_fields`) + domain validators (firewall, device settings) |
| `pyvelo.config.(firewall\|device_settings\|qos\|wan\|control_plane)` | Per-module configuration helpers |
| `pyvelo.config.diff` | Generic keyed-list diff engine: `diff_intents`, `DiffResult`, `Added`, `Removed`, `Modified`, `Unchanged`, `FieldChange`. Each side must have each key once (`ValueError` otherwise). |
| `pyvelo.config.errors` | `IntentError`, `ConfigInputError`, `LayerNotSavedError`, `SaveStepError`, `SaveValidationError`, `UnknownRegistryEntryError` |
| `pyvelo.testing` | Test fixtures: `MockVcoClient`, which holds a `ModuleStore` (`pyvelo.testing`) as `store` that serves an edge's configuration stack, module reads and module writes, so a second `load` sees what a save stored (`MockVcoClient.for_state(state)` seeds it from a state), `make_firewall_module`, `make_firewall_segment` (both build an edge-layer object unless given `layer=Profile`) |

## Tests

```bash
uv run pytest packages/pyvelo/tests/config
uv run basedpyright packages/pyvelo
uv run ruff check packages/pyvelo
```

`packages/pyvelo/tests/config/test_layer_typing.py` is checked by basedpyright
with `reportUnnecessaryTypeIgnoreComment` on (see pyvelo's "Tests"): it fixes
the layer each `ConfigState` view gives (`state.edge.firewall` is
`FirewallData[Edge]`, `state.profile.firewall` is `FirewallData[Profile]`, and
`state.edge.qos` is `QosData[Edge]`).

The live comparison of "The effective view" is
`packages/pyvelo/tests/integration/test_effective_live.py`. It only
reads, runs under `-m integration` with `VCO_HOST` and `VCO_TOKEN` set, and
prints the comparison of every edge (run it with `-s`), whose `factory`,
`ignored` and `unconverted` entries it records; a finding fails it.
