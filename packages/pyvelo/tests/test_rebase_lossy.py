from typing import Annotated

from pydantic import MISSING, Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.rebase import Path, find_lossy_assignments

from .test_rebase import Root


class Opt(VcoModel):
    label: str | None = None
    extra: int | MISSING = MISSING


def dump(m: VcoModel) -> dict[str, object]:
    return m.model_dump(mode="json", by_alias=True)


def lossy(base: VcoModel, cur: VcoModel) -> list[Path]:
    return find_lossy_assignments(base, cur, dump(base), dump(cur))


RAW = {
    "segments": [{"segment": {"segmentId": 0, "name": "A"}, "rules": [{"name": "r1"}]}]
}


def test_assigning_the_default_on_an_absent_key_is_lossy():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.logging_enabled = False
    assert lossy(base, cur) == ["logging_enabled"]


def test_assigning_a_different_value_is_not_lossy():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.logging_enabled = True
    assert lossy(base, cur) == []


def test_key_present_in_raw_is_not_lossy():
    raw = {**RAW, "loggingEnabled": False}
    base, cur = Root.model_validate(raw), Root.model_validate(raw)
    cur.logging_enabled = False
    assert lossy(base, cur) == []


def test_nested_through_keyed_lists():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.segments[0].rules[0].port = 0
    assert lossy(base, cur) == ["segments[A].rules[r1].port"]


def test_missing_and_none_are_not_lossy():
    base, cur = Opt.model_validate({}), Opt.model_validate({})
    cur.label = None
    cur.extra = MISSING
    assert lossy(base, cur) == []


def test_items_only_in_current_are_ignored():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.segments.append(
        type(cur.segments[0]).model_validate({"segment": {"segmentId": 1, "name": "B"}})
    )
    assert lossy(base, cur) == []


# --- behaviour the cases above do not pin ----------------------------------


class Inner(VcoModel):
    x: int = 0
    y: int = 0


class Outer(VcoModel):
    inner: Inner = Field(default_factory=Inner)
    items: list[Inner] = Field(default_factory=list)


def test_nested_model_absent_from_raw_is_walked():
    base, cur = Outer.model_validate({}), Outer.model_validate({})
    cur.inner.x = 0
    assert lossy(base, cur) == ["inner.x"]


def test_assigned_model_equal_to_its_default_is_one_path():
    base, cur = Outer.model_validate({}), Outer.model_validate({})
    cur.inner = Inner(x=0)
    assert lossy(base, cur) == ["inner"]


def test_field_absent_from_raw_and_changed_is_written_whole():
    base, cur = Outer.model_validate({}), Outer.model_validate({})
    cur.inner = Inner(x=0, y=5)  # x is the default, but the whole value is written
    assert lossy(base, cur) == []


def test_unkeyed_list_pairs_by_position_when_lengths_match():
    raw = {"items": [{"y": 1}, {"x": 1}]}
    base, cur = Outer.model_validate(raw), Outer.model_validate(raw)
    cur.items[0].x = 0
    cur.items[1].y = 0
    assert lossy(base, cur) == ["items[#0].x", "items[#1].y"]


def test_unkeyed_list_of_other_length_is_not_walked():
    raw = {"items": [{"y": 1}]}
    base, cur = Outer.model_validate(raw), Outer.model_validate(raw)
    cur.items[0].x = 0
    cur.items.append(Inner(y=2))
    assert lossy(base, cur) == []


def test_keyed_items_are_paired_by_key_not_position():
    raw = {
        "segments": [
            {"segment": {"segmentId": 0, "name": "A"}, "rules": [{"name": "r1"}]},
            {"segment": {"segmentId": 1, "name": "B"}, "rules": [{"name": "r1"}]},
        ]
    }
    base, cur = Root.model_validate(raw), Root.model_validate(raw)
    cur.segments.reverse()
    cur.segments[0].rules[0].port = 0  # segment B
    assert lossy(base, cur) == ["segments[B].rules[r1].port"]


def test_keyed_list_with_repeated_keys_is_walked_by_position_when_unchanged():
    # rebase keeps raw whole when the list is unchanged, so the assignment is dropped
    raw = {
        "segments": [
            {
                "segment": {"segmentId": 0, "name": "A"},
                "rules": [{"name": "r"}, {"name": "r"}],
            }
        ]
    }
    base, cur = Root.model_validate(raw), Root.model_validate(raw)
    cur.segments[0].rules[0].port = 0
    assert lossy(base, cur) == ["segments[A].rules[#0].port"]


def test_keyed_list_with_repeated_keys_changed_elsewhere_is_not_walked():
    # a changed list is written whole from the dump, so the assignment is kept
    raw = {
        "segments": [
            {
                "segment": {"segmentId": 0, "name": "A"},
                "rules": [{"name": "r"}, {"name": "r"}],
            }
        ]
    }
    base, cur = Root.model_validate(raw), Root.model_validate(raw)
    cur.segments[0].rules[0].port = 0
    cur.segments[0].rules[1].port = 9
    assert lossy(base, cur) == []


class Opt2(VcoModel):
    name: str | None = None
    port: int = 0


class Opt2s(VcoModel):
    rules: Annotated[list[Opt2], KeyField("name")] = Field(default_factory=list)


def test_keyed_list_with_a_missing_key_is_walked_by_position_when_unchanged():
    raw = {"rules": [{"name": "r1"}, {"port": 1}]}
    base, cur = Opt2s.model_validate(raw), Opt2s.model_validate(raw)
    cur.rules[0].port = 0
    assert lossy(base, cur) == ["rules[#0].port"]


def test_keyed_list_with_a_missing_key_changed_elsewhere_is_not_walked():
    raw = {"rules": [{"name": "r1"}, {"port": 1}]}
    base, cur = Opt2s.model_validate(raw), Opt2s.model_validate(raw)
    cur.rules[0].port = 0
    cur.rules[1].port = 9
    assert lossy(base, cur) == []


def test_unkeyed_list_changed_elsewhere_is_not_walked():
    # the list is written whole from the dump, which includes the assigned default
    raw = {"items": [{"y": 1}]}
    base, cur = Outer.model_validate(raw), Outer.model_validate(raw)
    cur.items[0].x = 0
    cur.items[0].y = 7
    assert lossy(base, cur) == []


def test_unkeyed_list_unchanged_is_walked_by_position():
    raw = {"items": [{"y": 1}]}
    base, cur = Outer.model_validate(raw), Outer.model_validate(raw)
    cur.items[0].x = 0
    assert lossy(base, cur) == ["items[#0].x"]


def test_item_whose_key_changed_is_not_paired():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.segments[0].segment.name = "Z"
    cur.segments[0].rules[0].port = 0
    assert lossy(base, cur) == []


def test_dumps_that_do_not_line_up_with_the_model_are_not_walked():
    base, cur = Root.model_validate(RAW), Root.model_validate(RAW)
    cur.segments[0].rules[0].port = 0
    assert find_lossy_assignments(base, cur, {}, {}) == []
    wrong = {"segments": [dump(base)["segments"], "extra"]}
    assert find_lossy_assignments(base, cur, wrong, wrong) == []
