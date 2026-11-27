"""Random edits of a keyed list: every change is shown as the item it is about.

Each case is a sequence of edits of one list of a real module, drawn by a
seeded generator: adds, deletes, renames and moves by patch, with names that
read as another item's key, alt key, label or position, and inserts,
replacements and new keys through the model, which make keys repeat. For
every change of the plan, the item it is about is found here from positions:
the documents the save compared are paired again, the way the save pairs
them, and the item at that position of the data as read or as planned is
named by ``item_part``. What the plan shows must name exactly that item, in
the path, the place, the moves and the names of an order entry; every shown
path must parse back to itself; and the plan text and the field changes must
agree line by line.
"""

import random
from dataclasses import dataclass
from typing import Any

import pytest
from hub_state import edit
from pydantic import MISSING
from pyvelo.client.exceptions import VcoError
from pyvelo.client.module_write import ModulePlan
from pyvelo.config import ConfigFieldChange, ConfigPipeline, add, move
from pyvelo.config.state import ConfigPlan, ConfigState
from pyvelo.core import VcoModel, delete, item_part
from pyvelo.core.base import is_dict
from pyvelo.core.rebase import (
    ItemMove,
    ItemPart,
    ItemPlace,
    Key,
    KeyedList,
    Path,
    RawChange,
    item_names,
    item_parts,
    item_text,
    node_child,
    pair_items,
)
from pyvelo.core.show import moves_text
from test_patch_real_models import (  # pyright: ignore[reportImplicitRelativeImport]
    PROFILE_DS,
    SEGMENT,
    desc,
    firewall,
    fw_rule,
    make_state,
)

SEEDS = 60
"""The cases of each kind: enough to reach every kind of change, in about a second."""

RULE_NAMES = ["a", "b", "c", "la", "lb", "#0", "#1", "#2", "", "x.y", "x]y", "[z", "24"]
"""Names for a rule: those of others, their logical IDs, positions, odd texts."""
NETWORK_NAMES = ["a", "n", "1", "2", "3", "5", "7", "#0", "#1", "", "x.y", "x]y"]
"""Names for a network: those of others, their VLANs, positions, odd texts."""
PLACES: list[dict[str, Any]] = [{}, {"first": True}, {"last": True}]


@dataclass
class Case:
    """One list of a state, and what was done to it."""

    state: ConfigState
    module: str
    """The module's name, as the VCO has it."""
    prefix: Path
    """The list's path, as a change writes it."""
    log: list[object]

    def items(self) -> list[Any]:
        """The list in the data the edits are made to."""
        if self.module == "firewall":
            return self.state.edge.firewall.segment(SEGMENT).outbound
        return self.state.edge.device_settings.lan.networks

    @property
    def field(self) -> str:
        """The list's field, as python and the VCO both name it."""
        return "outbound" if self.module == "firewall" else "networks"

    def owner(self, data: Any) -> Any:
        """What holds the list in *data*, the module's data as a model or as JSON."""
        as_json = is_dict(data)
        if self.module == "firewall":
            segments: Any = data["segments"] if as_json else data.segments
            return segments[0]
        return data["lan"] if as_json else data.lan

    async def patch(self, patch: dict[str, Any]) -> None:
        """Apply *patch* to the list; a refused patch is noted and changes nothing."""
        self.log.append(patch)
        try:
            if self.module == "firewall":
                _ = await ConfigPipeline(None).apply(
                    self.state,
                    module="firewall",
                    segment=SEGMENT,
                    path="outbound",
                    patch=patch,
                )
            else:
                await edit(self.state, self.module, {"lan": {"networks": patch}})
        except (VcoError, ValueError, TypeError) as refused:
            self.log.append(f"refused: {type(refused).__name__}")


def rule(rng: random.Random, name: str | None = None) -> dict[str, Any]:
    name = rng.choice(RULE_NAMES) if name is None else name
    row = fw_rule(name, ruleLogicalId=f"l{name}")
    if rng.random() < 0.3:
        del row["ruleLogicalId"]
    return row


def network(rng: random.Random) -> dict[str, Any]:
    vlan_id = rng.choice([1, 1, 2, 3, 7])
    row: dict[str, Any] = {
        "vlanId": vlan_id,
        "name": rng.choice(NETWORK_NAMES),
        "segmentId": 0,
        "cidrIp": f"10.{vlan_id}.{rng.randrange(250)}.0",
        "cidrPrefix": 24,
    }
    if rng.random() < 0.3:
        row["cost"] = 10
    return row


async def made(kind: str, rng: random.Random) -> Case:
    count = rng.randrange(1, 6)
    if kind == "firewall":
        # A name may repeat: the rules of one name differ by their logical IDs.
        rules = [rule(rng, rng.choice("abcd")) for _ in range(count)]
        for n, row in enumerate(rules):
            if "ruleLogicalId" in row and row in rules[:n]:
                row["ruleLogicalId"] = f"l{row['name']}{n}"
        state = make_state({"firewall": firewall(*rules)}, {"firewall": firewall()})
        prefix = Path(("segments", Key(SEGMENT), "outbound"))
        return Case(state, "firewall", prefix, [rules])
    networks = [network(rng) for _ in range(count)]
    settings = {
        "lan": {"networks": networks, "interfaces": []},
        "segments": [{"segment": desc(0, SEGMENT)}],
    }
    state = make_state({"deviceSettings": settings}, {"deviceSettings": PROFILE_DS})
    return Case(state, "deviceSettings", Path(("lan", "networks")), [networks])


def selector(rng: random.Random, case: Case) -> str:
    """A selector a patch may give: a name or a key in use, or a position."""
    items = case.items()
    names = [str(item.name) for item in items]
    if case.module != "firewall":
        names += [str(item.vlan_id) for item in items]
    return rng.choice([*names, "#0", "#1", "#2"])


async def patched(rng: random.Random, case: Case) -> None:
    """One edit by patch: an add, a delete, a rename or a move."""
    op = rng.choice(["add", "add", "delete", "rename", "move"])
    if op == "add" and case.module == "firewall":
        body = {"name": rng.choice(RULE_NAMES), "action": {}, "match": {}}
        await case.patch({"+new": add(body, **rng.choice(PLACES))})
    elif op == "add":
        body = {"name": rng.choice(NETWORK_NAMES), "segment_id": 0}
        key = str(rng.choice([1, 2, 3, 5, 7]))
        await case.patch({key: add(body, **rng.choice(PLACES))})
    elif op == "delete":
        await case.patch({selector(rng, case): delete()})
    elif op == "rename":
        names = RULE_NAMES if case.module == "firewall" else NETWORK_NAMES
        await case.patch({selector(rng, case): {"name": rng.choice(names)}})
    else:
        place: dict[str, Any] = rng.choice(
            [{"first": True}, {"last": True}, {"after": selector(rng, case)}]
        )
        await case.patch({selector(rng, case): move(**place)})


def modelled(rng: random.Random, case: Case) -> None:
    """One edit through the model: these make a key repeat, which no patch does."""
    items = case.items()
    if not items:
        return
    cls = type(items[0])
    at = rng.randrange(len(items))
    op = rng.choice(["insert", "insert", "replace", "rekey", "delete", "shuffle"])
    case.log.append((op, at))
    if op == "insert":
        row = rule(rng) if case.module == "firewall" else network(rng)
        items.insert(rng.randrange(len(items) + 1), cls.model_validate(row))
    elif op == "replace":
        row = items[at].model_dump(mode="json", by_alias=True, exclude_unset=True)
        _ = row.pop(rng.choice(["cost", "ruleLogicalId", "none"]), None)
        items[at] = cls.model_validate(row)
    elif op == "rekey" and case.module == "firewall":
        items[at].name = rng.choice(RULE_NAMES)
    elif op == "rekey":
        items[at].vlan_id = rng.choice([1, 2, 3, 9])
    elif op == "delete":
        del items[at]
    else:
        rng.shuffle(items)


@dataclass
class Truth:
    """The items of a list as the save paired them, each named where it is."""

    old_parts: list[ItemPart]
    names: dict[int, ItemPart]
    new_of: dict[int, int]
    read: VcoModel
    """What holds the list in the data as read."""
    planned: VcoModel
    """What holds the list in the data as planned."""
    field: str

    def new(self, j: int) -> ItemPart:
        """The part that names the item at *j* as planned, else its position."""
        part = item_part(self.planned, self.field, j)
        return j if part is None else part

    def old(self, i: int) -> ItemPart:
        """The part that names the item at *i* as read, else its position."""
        part = item_part(self.read, self.field, i)
        return i if part is None else part

    def place(self, j: int) -> ItemPlace:
        return ItemPlace(self.new(j - 1) if j else None)

    def at(self, name: ItemPart) -> int:
        """The position as planned of the item a change names *name*."""
        (j,) = [j for j, part in self.names.items() if part == name]
        return j

    def read_at(self, name: ItemPart) -> int:
        """The position as read of the item a change names *name*."""
        (i,) = [i for i, part in enumerate(self.old_parts) if part == name]
        return i

    def shown(self, change: RawChange, prefix: Path) -> RawChange:
        """*change*, a change of the list or of an item of it, as it should be shown.

        Its path is given up to the item, and its values are left out.
        """
        rest = change.path.parts[len(prefix.parts) :]
        if not rest:
            assert change.moves is not None, "a paired list is not replaced whole"
            order = [self.new(self.new_of[i]) for i in sorted(self.new_of)]
            return RawChange(
                prefix,
                [item_text(part) for part in order],
                [item_text(self.new(j)) for j in range(len(self.names))],
                moves=tuple(
                    ItemMove(self.new(j), self.place(j))
                    for j in (self.at(m.item) for m in change.moves)
                ),
            )
        name = rest[0]
        assert not isinstance(name, str)
        if change.before is MISSING and len(rest) == 1:
            j = self.at(name)
            assert j not in self.new_of.values()
            return RawChange(prefix.child(self.new(j)), None, None, place=self.place(j))
        i = self.read_at(name)
        if change.after is MISSING and len(rest) == 1:
            assert i not in self.new_of
            return RawChange(prefix.child(self.old(i)), None, None)
        return RawChange(prefix.child(self.new(self.new_of[i])), None, None)


def truth(case: Case, plan: ModulePlan, as_read: object) -> Truth | None:
    """How the save paired the list; None where it replaced the list whole."""
    field = case.field
    read, planned = case.owner(plan.as_read), case.owner(plan.planned)
    old, new = case.owner(as_read)[field], case.owner(plan.data)[field]
    node = node_child(type(read), field)
    assert isinstance(node, KeyedList)
    assert len(old) == len(getattr(read, field))
    assert len(new) == len(getattr(planned, field))
    pairing = pair_items(old, new, node)
    names = None if pairing is None else item_names(old, new, node, pairing)
    if pairing is None or names is None:
        return None
    parts = item_parts(old, node)
    return Truth(parts, names, dict(pairing.pairs), read, planned, field)


def line(change: ConfigFieldChange) -> str:
    """The start of the plan's line for *change*, up to its values."""
    if change.moves is not None:
        return f"  ~ {change.path}: order: {moves_text(change.moves)}"
    if change.after is MISSING:
        return f"  - {change.path}"
    if change.before is MISSING:
        place = "" if change.place is None else f" ({change.place})"
        return f"  + {change.path}{place}: "
    return f"  ~ {change.path}: "


def check(case: Case, planned: ConfigPlan) -> int:
    """Check every change of the list in *planned*, the plan; the number checked."""
    state = case.state
    plans = [m for m in planned.modules if m.name == case.module]
    config = state.target_config
    as_read: object = getattr(config, _ATTRIBUTE[case.module]).raw_data
    checked = 0
    for plan in plans:
        fields = state.get_field_changes(case.module)
        assert len(fields) == len(plan.changes)
        lines = plan.render().splitlines()[1 : 1 + len(plan.changes)]
        found = truth(case, plan, as_read)
        shown_prefix = plan.named(case.prefix)
        for change, field, text in zip(plan.changes, fields, lines, strict=True):
            seen = plan.shown_change(change)
            assert (field.path, field.place, field.moves) == (
                seen.path,
                seen.place,
                seen.moves,
            )
            assert Path.parse(str(seen.path)).parts == seen.path.parts
            assert text.startswith(line(field)), (text, line(field))
            if change.path.parts[: len(case.prefix.parts)] != case.prefix.parts:
                continue
            if found is None:
                assert change.path == case.prefix
                assert change.moves is None
                continue
            want = found.shown(change, shown_prefix)
            head = Path(seen.path.parts[: len(want.path.parts)])
            assert (str(head), seen.place, seen.moves) == (
                str(want.path),
                want.place,
                want.moves,
            ), f"{change.path} place {change.place} moves {change.moves}"
            if change.moves is not None:
                assert (seen.before, seen.after) == (want.before, want.after)
            checked += 1
    return checked


_ATTRIBUTE = {"firewall": "firewall", "deviceSettings": "device_settings"}


async def run(kind: str, seed: int) -> int:
    """Make the case of *seed*, edit it and check its plan; the changes checked."""
    rng = random.Random(f"{kind}-{seed}")
    case = await made(kind, rng)
    for _ in range(rng.randrange(1, 5)):
        if rng.random() < 0.5:
            await patched(rng, case)
        else:
            modelled(rng, case)
    try:
        planned = case.state.plan()
    except (VcoError, ValueError):
        return 0  # the edits make a module no plan is made of
    try:
        return check(case, planned)
    except AssertionError as failed:
        raise AssertionError(f"{kind} {seed}: {failed}\nedits: {case.log}") from failed


@pytest.mark.parametrize("kind", ["firewall", "deviceSettings"])
async def test_every_change_of_random_edits_names_the_item_it_is_about(
    kind: str,
) -> None:
    counts = [await run(kind, seed) for seed in range(SEEDS)]
    # The cases are not vacuous: many have changes of the list to check.
    assert sum(counts) >= SEEDS // 2
    assert sum(1 for count in counts if count) >= SEEDS // 3
