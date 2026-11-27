"""A module row read back from its named dump is the row its wire dump gives.

``named_dump`` shows a row by attribute names with its secrets masked, and
``named_validate`` reads that back with the row as ``stored``. A row keeps
the JSON it was validated from (``raw_data``, ``raw_refs``), and a save builds
its payload from that, so the row read back must keep it in the VCO's keys
with the complete secrets: exactly what the row validated from its own wire
dump keeps. That is checked here for every row of three states, at the edge's
level and the profile's, unedited and under random edits.
"""

import copy
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import pytest
from hub_state import edge_over_a_hub_profile, rule_uses_hub
from pydantic import MISSING
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    DeviceSettingsConfigurationModule,
)
from pyvelo.core import HIDDEN, ConfigContext, VcoModel
from pyvelo.core.base import is_dict, is_list
from test_state_plan import make_state  # pyright: ignore[reportImplicitRelativeImport]


def wire_of(model: VcoModel) -> dict[str, Any]:
    """The wire dump of *model*, as its JSON text reads back."""
    return json.loads(json.dumps(model.model_dump(mode="json", by_alias=True)))


def named_of(model: VcoModel) -> dict[str, Any]:
    """The named dump of *model*, as its JSON text reads back."""
    return json.loads(json.dumps(model.named_dump()))


async def every_fixture_row() -> list[ConfigurationModuleBase]:
    """Each module row of three states, at the edge's level and the profile's."""
    rows: list[ConfigurationModuleBase] = []
    for state in (await rule_uses_hub(), make_state(), await edge_over_a_hub_profile()):
        for config in (state.edge_config, state.profile_config):
            if config is not None and config.modules is not MISSING:
                rows.extend(config.modules)
    return rows


def rebuilt[M: VcoModel](
    row: M, named: dict[str, Any], context: dict[str, object] | None = None
) -> M:
    """*row*'s class validated from *named*, with *row* as the stored model."""
    return type(row).named_validate(named, stored=row, context=context)


async def test_the_fixture_rows_are_of_every_module_class_the_states_hold() -> None:
    rows = await every_fixture_row()
    assert sorted({type(row).__name__ for row in rows}) == [
        "DeviceSettingsConfigurationModule",
        "FirewallConfigurationModule[Edge]",
        "FirewallConfigurationModule[Profile]",
        "QosConfigurationModule[Edge]",
        "QosConfigurationModule[Profile]",
    ]
    assert len(rows) == 11
    assert any(HIDDEN in json.dumps(named_of(row).get("refs")) for row in rows)


async def test_a_row_rebuilt_from_its_named_dump_keeps_the_vcos_keys() -> None:
    for row in await every_fixture_row():
        wire = wire_of(row)
        named = named_of(row)
        assert json.dumps(type(row).vco_document(named, stored=row)) == json.dumps(wire)
        by_keys = type(row).model_validate(wire)
        by_name = rebuilt(row, named)
        name = type(row).__name__
        assert json.dumps(by_name.raw_data) == json.dumps(by_keys.raw_data), name
        assert (by_name.raw_refs is MISSING) == (by_keys.raw_refs is MISSING)
        if by_keys.raw_refs is not MISSING:
            assert json.dumps(by_name.raw_refs) == json.dumps(by_keys.raw_refs)
        dumped = by_name.model_dump(mode="json", by_alias=True)
        assert dumped == by_keys.model_dump(mode="json", by_alias=True)


async def test_a_row_rebuilt_from_either_dump_holds_only_what_the_models_declare() -> (
    None
):
    # The edge firewall of ``make_state`` was read with a key no model declares.
    (row, *_) = [r for r in await every_fixture_row() if r.version == "e1"]
    assert "futureKey" in json.dumps(row.raw_data)
    by_keys = type(row).model_validate(wire_of(row))
    by_name = rebuilt(row, named_of(row))
    assert "futureKey" not in json.dumps(by_keys.raw_data)
    assert "futureKey" not in json.dumps(by_name.raw_data)
    assert json.dumps(by_name.raw_data) != json.dumps(row.raw_data)


async def test_the_context_reaches_the_row() -> None:
    (row, *_) = [
        r
        for r in await every_fixture_row()
        if isinstance(r, DeviceSettingsConfigurationModule)
    ]
    named = named_of(row)
    unloaded = rebuilt(row, named, {"refs_loaded": False})
    assert unloaded.refs_loaded is False
    assert rebuilt(row, named).refs_loaded is True
    level: dict[str, object] = {"config_level": ConfigContext.PROFILE}
    assert rebuilt(row, named, level).data.config_context is ConfigContext.PROFILE
    assert rebuilt(row, named).data.config_context is None


CONTEXTS: list[dict[str, object]] = [
    {"refs_loaded": False},
    {"config_level": ConfigContext.EDGE},
    {"config_level": ConfigContext.PROFILE, "refs_loaded": False},
]


@pytest.mark.parametrize("context", CONTEXTS)
async def test_a_row_rebuilt_with_a_context_is_the_row_model_validate_gives(
    context: dict[str, object],
) -> None:
    for row in await every_fixture_row():
        by_keys = type(row).model_validate(wire_of(row), context=context)
        by_name = rebuilt(row, named_of(row), context)
        assert by_name.refs_loaded is by_keys.refs_loaded
        assert by_name.config_context is by_keys.config_context
        assert json.dumps(by_name.raw_data) == json.dumps(by_keys.raw_data)
        dumped = by_name.model_dump(mode="json", by_alias=True)
        assert dumped == by_keys.model_dump(mode="json", by_alias=True)


type Kind = Literal["replace", "delete", "swap", "copy"]
"""An edit: replace a scalar by another of its type; delete an entry of an object or
an item of a list; swap two items of a list; copy an item to the end of its list."""

type Steps = list[int]
"""A place of a JSON document: the n-th entry of an object, the n-th item of a list."""


@dataclass(frozen=True)
class Edit:
    """One edit of a JSON document, made at a place of it."""

    kind: Kind
    value: object = None
    """The scalar a ``replace`` writes."""
    other: int = 0
    """The position of the item a ``swap`` exchanges the item with."""


def holds_a_mask(value: object) -> bool:
    if is_dict(value):
        return any(holds_a_mask(item) for item in value.values())
    if is_list(value):
        return any(holds_a_mask(item) for item in value)
    return isinstance(value, str) and value == HIDDEN


def held(holder: Any, step: int) -> Any:
    """What *holder* has at *step*: its n-th item, or the value of its n-th entry."""
    return holder[step] if is_list(holder) else holder[list(holder)[step]]


def edits_at(
    value: object, steps: Steps, *, in_list: int | None
) -> Iterator[tuple[Steps, Kind]]:
    """The edits of *value*, found at *steps*: of it, then of what is below it.

    *in_list* is the length of the list *value* is an item of, None for an
    entry of an object. A mask is left as it is, and so is a list that holds
    one, with all below it: which stored item an item of such a list is
    depends on the item (``restore_secrets``).
    """
    if isinstance(value, str) and value == HIDDEN:
        return
    yield steps, "delete"
    if in_list is not None:
        yield steps, "copy"
        if in_list > 1:
            yield steps, "swap"
    if is_dict(value):
        for position, item in enumerate(value.values()):
            yield from edits_at(item, [*steps, position], in_list=None)
    elif is_list(value):
        if not holds_a_mask(value):
            for position, item in enumerate(value):
                yield from edits_at(item, [*steps, position], in_list=len(value))
    elif value is not None:
        yield steps, "replace"


def fresh(rng: random.Random, leaf: object) -> object:
    """A scalar of the type of *leaf* that is not *leaf*, and no mask."""
    if isinstance(leaf, bool):
        return not leaf
    if isinstance(leaf, int):
        return leaf + rng.randrange(1, 1000)
    if isinstance(leaf, float):
        return leaf + rng.randrange(1, 1000) / 8
    return f"edit-{rng.randrange(10**6)}"


def drawn(rng: random.Random, named: dict[str, Any]) -> tuple[Steps, Edit] | None:
    """One edit of a row's document, drawn by *rng*; None when it allows none.

    *named* is the named dump, which shows where the masks are. Nothing under
    the row's ``refs`` is edited: each ref row holds a mask. The kind of edit
    is drawn first, so that the few items of lists are edited as often as the
    many scalars.
    """
    found: list[tuple[Steps, Kind]] = []
    for position, (key, item) in enumerate(named.items()):
        if key != "refs":
            found.extend(edits_at(item, [position], in_list=None))
    if not found:
        return None
    kinds: list[Kind] = sorted({kind for _, kind in found})
    kind = rng.choice(kinds)
    steps = rng.choice([steps for steps, its_kind in found if its_kind == kind])
    holder: Any = named
    for step in steps[:-1]:
        holder = held(holder, step)
    if kind == "replace":
        return steps, Edit(kind, value=fresh(rng, held(holder, steps[-1])))
    if kind == "swap":
        others = [n for n in range(len(holder)) if n != steps[-1]]
        return steps, Edit(kind, other=rng.choice(others))
    return steps, Edit(kind)


def apply_at(document: dict[str, Any], steps: Steps, edit: Edit) -> None:
    """Make *edit* at *steps* of *document*."""
    *above, last = steps
    holder: Any = document
    for step in above:
        holder = held(holder, step)
    if is_list(holder):
        if edit.kind == "delete":
            del holder[last]
        elif edit.kind == "copy":
            holder.append(copy.deepcopy(holder[last]))
        elif edit.kind == "swap":
            holder[last], holder[edit.other] = holder[edit.other], holder[last]
        else:
            holder[last] = edit.value
        return
    key = list(holder)[last]
    if edit.kind == "delete":
        del holder[key]
    else:
        holder[key] = edit.value


def test_an_edit_is_made_at_the_same_place_of_two_documents_of_one_shape() -> None:
    wire: dict[str, Any] = {"aB": [{"cD": 1}, {"cD": 2}], "eF": {"gH": "x"}}
    named: dict[str, Any] = {"a_b": [{"c_d": 1}, {"c_d": 2}], "e_f": {"g_h": "x"}}
    for steps, edit in (
        ([0, 1, 0], Edit("replace", value=3)),
        ([0, 0], Edit("swap", other=1)),
        ([0, 1], Edit("copy")),
        ([1, 0], Edit("delete")),
        ([0, 0], Edit("delete")),
    ):
        apply_at(wire, steps, edit)
        apply_at(named, steps, edit)
    assert wire == {"aB": [{"cD": 1}, {"cD": 1}], "eF": {}}
    assert named == {"a_b": [{"c_d": 1}, {"c_d": 1}], "e_f": {}}


def test_no_edit_is_drawn_at_a_mask_or_in_a_list_that_holds_one() -> None:
    named: dict[str, Any] = {
        "name": "n",
        "secret": "***",
        "users": [{"name": "u", "passphrase": "***"}, {"name": "v"}],
        "refs": {"rows": [{"id": 1}]},
    }
    rng = random.Random(0)
    places: set[tuple[int, ...]] = set()
    for _ in range(300):
        found = drawn(rng, named)
        assert found is not None
        places.add(tuple(found[0]))
    assert places == {(0,), (2,)}
    assert drawn(rng, {"secret": "***", "refs": {"id": 1}}) is None


def test_each_kind_of_edit_is_drawn_and_a_scalar_keeps_its_type() -> None:
    named: dict[str, Any] = {
        "flag": True,
        "count": 1,
        "items": ["a", "b"],
        "gone": None,
    }
    rng = random.Random(0)
    kinds: set[str] = set()
    for _ in range(100):
        found = drawn(rng, named)
        assert found is not None
        steps, edit = found
        kinds.add(edit.kind)
        if edit.kind == "replace":
            leaf = held(named if len(steps) == 1 else named["items"], steps[-1])
            assert type(edit.value) is type(leaf)
            assert edit.value != leaf
        if edit.kind == "swap":
            assert steps == [2, 1 - edit.other]
    assert kinds == {"replace", "delete", "swap", "copy"}


@pytest.mark.parametrize("seed", range(60))
async def test_an_edit_of_the_named_dump_is_the_same_edit_of_the_wire_dump(
    seed: int,
) -> None:
    rng = random.Random(seed)
    for row in await every_fixture_row():
        wire = wire_of(row)
        named = named_of(row)
        made = 0
        for _ in range(rng.randrange(1, 6)):
            edit = drawn(rng, named)
            if edit is not None:
                apply_at(wire, *edit)
                apply_at(named, *edit)
                made += 1
        assert made > 0
        assert json.dumps(type(row).vco_document(named, stored=row)) == json.dumps(wire)
