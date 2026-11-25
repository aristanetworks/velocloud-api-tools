import uuid
from typing import Annotated, Any

from pydantic import MISSING, Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.rebase import carry_unset_defaults


class Tagged(VcoModel):
    name: str
    tag: uuid.UUID = Field(default_factory=uuid.uuid4)
    note: str = "n"


class Ided(VcoModel):
    name: str
    rid: str | MISSING = MISSING
    tag: uuid.UUID = Field(default_factory=uuid.uuid4)


class Holder(VcoModel):
    tag: uuid.UUID = Field(default_factory=uuid.uuid4)
    inner: Tagged = Field(default_factory=lambda: Tagged(name="in"))
    items: Annotated[list[Tagged], KeyField("name")] = Field(default_factory=list)
    plain: list[Tagged] = Field(default_factory=list)
    ided: Annotated[list[Ided], KeyField("name", alt="rid")] = Field(
        default_factory=list
    )


def dump(m: VcoModel) -> dict[str, Any]:
    return m.model_dump(mode="json", by_alias=True)


def carried(base: Holder, cur: Holder) -> tuple[dict[str, Any], dict[str, Any]]:
    before, after = dump(base), dump(cur)
    carry_unset_defaults(base, cur, before, after)
    return before, after


def pair(raw: dict[str, Any]) -> tuple[Holder, Holder]:
    return Holder.model_validate(raw), Holder.model_validate(raw)


def test_a_scalar_neither_model_set_takes_its_value_from_before() -> None:
    base, cur = pair({})
    assert dump(base)["tag"] != dump(cur)["tag"]
    before, after = carried(base, cur)
    assert after == before


def test_nested_models_and_lists_are_walked() -> None:
    raw = {
        "items": [{"name": "a"}, {"name": "b", "tag": str(uuid.uuid4())}],
        "plain": [{"name": "x"}, {"name": "y"}],
    }
    base, cur = pair(raw)
    before, after = carried(base, cur)
    assert after == before


def test_a_keyed_list_is_paired_by_key_not_position() -> None:
    base, cur = pair({"items": [{"name": "a"}, {"name": "b"}]})
    cur.items.reverse()
    before, after = carried(base, cur)
    assert [i["name"] for i in after["items"]] == ["b", "a"]
    assert after["items"][0]["tag"] == before["items"][1]["tag"]
    assert after["items"][1]["tag"] == before["items"][0]["tag"]


def test_an_item_only_the_caller_has_keeps_its_own_value() -> None:
    base, cur = pair({"items": [{"name": "a"}]})
    cur.items.append(Tagged(name="new"))
    before, after = carried(base, cur)
    assert after["items"][0] == before["items"][0]
    assert after["items"][1]["tag"] == dump(cur)["items"][1]["tag"]


def test_a_list_without_keys_is_paired_by_position_when_lengths_match() -> None:
    base, cur = pair({"plain": [{"name": "x"}, {"name": "y"}]})
    before, after = carried(base, cur)
    assert after["plain"] == before["plain"]


def test_a_list_without_keys_of_another_length_is_not_walked() -> None:
    base, cur = pair({"plain": [{"name": "x"}]})
    cur.plain.append(Tagged(name="y"))
    expected = dump(cur)["plain"]
    _, after = carried(base, cur)
    assert after["plain"] == expected


def test_a_value_the_caller_assigned_is_kept() -> None:
    base, cur = pair({})
    cur.tag = uuid.UUID(int=7)
    cur.inner.note = "changed"
    _, after = carried(base, cur)
    assert after["tag"] == str(uuid.UUID(int=7))
    assert after["inner"]["note"] == "changed"


def test_a_value_the_document_had_is_kept() -> None:
    raw = {"inner": {"name": "in", "tag": str(uuid.UUID(int=1))}}
    base, cur = pair(raw)
    cur.inner.tag = uuid.UUID(int=2)
    _, after = carried(base, cur)
    assert after["inner"]["tag"] == str(uuid.UUID(int=2))


def test_a_scalar_assigned_inside_an_unset_model_is_kept() -> None:
    base, cur = pair({})
    cur.inner.note = "changed"
    before, after = carried(base, cur)
    assert after["inner"]["note"] == "changed"
    assert after["inner"]["tag"] == before["inner"]["tag"]


def test_the_models_are_left_as_they_are() -> None:
    base, cur = pair({})
    tag = cur.tag
    _ = carried(base, cur)
    assert cur.tag == tag
    assert cur.model_fields_set == set()


def test_dumps_that_do_not_line_up_with_the_models_are_not_walked() -> None:
    base, cur = pair({"items": [{"name": "a"}]})
    before, after = dump(base), dump(cur)
    after["items"] = [after["items"][0], {"name": "extra"}]
    expected = dump(cur) | {"items": after["items"]}
    carry_unset_defaults(base, cur, before, after)
    assert after["items"] == expected["items"]


def test_lossy_and_carry_pair_by_alt_key() -> None:
    base, cur = pair({"ided": [{"name": "a", "rid": "1"}]})
    cur.ided[0].name = "b"
    before, after = carried(base, cur)
    assert after["ided"][0]["name"] == "b"
    assert after["ided"][0]["tag"] == before["ided"][0]["tag"]


def test_items_beside_a_removed_same_named_item_stay_paired() -> None:
    base, cur = pair(
        {
            "ided": [
                {"name": "a", "rid": "1"},
                {"name": "a", "rid": "2"},
                {"name": "b", "rid": "3"},
            ]
        }
    )
    del cur.ided[1]
    before, after = carried(base, cur)
    assert [i["rid"] for i in after["ided"]] == ["1", "3"]
    assert after["ided"][0]["tag"] == before["ided"][0]["tag"]
    assert after["ided"][1]["tag"] == before["ided"][2]["tag"]
