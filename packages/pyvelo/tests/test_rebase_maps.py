"""A map of models (``dict[K, Model]``): rebase, raw_changes, the lossy check and carrying walk into its values."""

import uuid
from typing import Annotated

from pydantic import MISSING, Field
from pyvelo.core import ConfigContext, EdgeOnly, VcoModel
from pyvelo.core.fields import KeyField, OverrideFlag
from pyvelo.core.rebase import (
    Path,
    RawChange,
    carry_unset_defaults,
    find_lossy_assignments,
    is_override_flag,
    raw_changes,
    rebase,
)


class Port(VcoModel):
    name: str
    speed: int = 0
    ping_response: bool = True  # dumps "pingResponse", also reads "ping_response"
    note: Annotated[str, EdgeOnly] = ""
    tag: uuid.UUID = Field(default_factory=uuid.uuid4)  # made anew by each parse
    override: Annotated[bool, OverrideFlag] = False


class Box(VcoModel):
    label: str = ""
    ports: Annotated[list[Port], KeyField("name")] = Field(default_factory=list)


class Boxes(VcoModel):
    boxes: dict[str, Box] = Field(default_factory=dict)


def port(name: str, **rest: object) -> dict[str, object]:
    return {"name": name, **rest}


def dumped(name: str, **rest: object) -> dict[str, object]:
    """A port as the model dumps it (without its generated tag)."""
    return {"name": name, "speed": 0, "pingResponse": True, "override": False, **rest}


def dump(m: VcoModel) -> dict[str, object]:
    return m.model_dump(mode="json", by_alias=True)


RAW = {"boxes": {"b1": {"ports": [{"name": "p1"}]}}}


def test_items_of_a_keyed_list_below_a_map_are_rebased_one_by_one():
    raw = {"boxes": {"b1": {"ports": [port("p1", x=1), port("p2", x=2)], "u": 1}}}
    before = {"boxes": {"b1": {"label": "", "ports": [dumped("p1"), dumped("p2")]}}}
    after = {
        "boxes": {"b1": {"label": "", "ports": [dumped("p1"), dumped("p2", speed=5)]}}
    }
    out = rebase(raw, before, after, Boxes)
    assert out == {
        "boxes": {"b1": {"ports": [port("p1", x=1), port("p2", x=2, speed=5)], "u": 1}}
    }
    assert raw_changes(raw, out, Boxes) == [
        RawChange("boxes.b1.ports[p2].speed", MISSING, 5)
    ]


def test_a_change_below_a_map_is_written_to_the_key_as_read():
    raw = {"boxes": {"b1": {"ports": [port("p1", ping_response=False)]}}}
    before = {
        "boxes": {"b1": {"label": "", "ports": [dumped("p1", pingResponse=False)]}}
    }
    after = {"boxes": {"b1": {"label": "", "ports": [dumped("p1")]}}}
    assert rebase(raw, before, after, Boxes) == {
        "boxes": {"b1": {"ports": [port("p1", ping_response=True)]}}
    }


def test_map_entries_added_and_removed():
    raw = {"boxes": {"b1": {"ports": [port("p1", x=1)], "u": 1}, "b2": {"u": 2}}}
    before = {
        "boxes": {
            "b1": {"label": "", "ports": [dumped("p1")]},
            "b2": {"label": "", "ports": []},
        }
    }
    new_box = {"label": "n", "ports": []}
    after = {"boxes": {"b1": before["boxes"]["b1"], "b3": new_box}}
    out = rebase(raw, before, after, Boxes)
    assert out == {"boxes": {"b1": raw["boxes"]["b1"], "b3": new_box}}
    assert raw_changes(raw, out, Boxes) == [
        RawChange("boxes.b2", {"u": 2}, MISSING),
        RawChange("boxes.b3", MISSING, new_box),
    ]


def test_the_default_of_an_absent_key_below_a_map_is_lossy():
    base, cur = Boxes.model_validate(RAW), Boxes.model_validate(RAW)
    cur.boxes["b1"].label = ""
    cur.boxes["b1"].ports[0].speed = 0
    cur.boxes["b2"] = Box(label="")  # an entry only the caller has is written whole
    before, after = dump(base), dump(cur)
    carry_unset_defaults(base, cur, before, after)
    assert find_lossy_assignments(base, cur, before, after) == [
        "boxes.b1.label",
        "boxes.b1.ports[p1].speed",
    ]


def test_a_field_the_level_omits_below_a_map_is_lossy():
    level = ConfigContext.PROFILE

    def at_level(m: VcoModel) -> dict[str, object]:
        return m.model_dump(mode="json", by_alias=True, context={"config_level": level})

    base, cur = Boxes.model_validate(RAW), Boxes.model_validate(RAW)
    cur.boxes["b1"].ports[0].note = "uplink"
    before, after = at_level(base), at_level(cur)
    carry_unset_defaults(base, cur, before, after)
    assert find_lossy_assignments(base, cur, before, after, level) == [
        "boxes.b1.ports[p1].note"
    ]


def test_a_generated_default_below_a_map_is_carried():
    base, cur = Boxes.model_validate(RAW), Boxes.model_validate(RAW)
    before, after = dump(base), dump(cur)
    assert after != before  # each parse made its own tag
    carry_unset_defaults(base, cur, before, after)
    assert after == before


def test_is_override_flag_follows_a_map():
    assert is_override_flag(Boxes, Path.parse("boxes.b1.ports[p1].override"))
    assert not is_override_flag(Boxes, Path.parse("boxes.b1.ports[p1].speed"))
    assert not is_override_flag(Boxes, Path.parse("boxes.b1.label"))
