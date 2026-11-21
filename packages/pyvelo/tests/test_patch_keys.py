"""Keys in a patch (spec 5.10.3 "Keys", "Duplicate keys"): alt keys and ``KeySpec``."""

from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.core import KeyField, KeySpec, PatchError, VcoModel, add, delete, move
from pyvelo.core.fields import get_key_spec
from pyvelo.core.rebase import Path

from .patch_models import Root, Rule


def two() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="b", rule_id="i2")])


def two_named_a() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="a", rule_id="i2")])


def names(root: Root) -> list[str]:
    return [rule.name for rule in root.rules]


def test_select_by_alt_key():
    root = two()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]

    root = two()
    _ = root.merge_patch({"rules": {"i1": delete()}})
    assert names(root) == ["b"]

    root = two()
    _ = root.merge_patch({"rules": {"a": move(after="i2")}})
    assert names(root) == ["b", "a"]


def test_key_wins_over_another_items_alt_key():
    root = Root(rules=[Rule(name="a", rule_id="b"), Rule(name="b", rule_id="i2")])
    _ = root.merge_patch({"rules": {"b": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_add_takes_the_key_not_the_alt_key():
    root = two()
    _ = root.merge_patch({"rules": {"i9": add()}})
    assert names(root) == ["a", "b", "i9"]
    assert root.rules[2].rule_id is MISSING


def test_duplicate_names_select_by_alt_key():
    root = two_named_a()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_duplicate_names_raise_and_list_alt_keys():
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": {"priority": 5}}})
    assert exc.value.path == Path.parse("rules[a]")
    assert "i1" in str(exc.value)
    assert "i2" in str(exc.value)
    assert [r.priority for r in root.rules] == [0, 0]


def test_add_of_a_duplicated_key_raises():
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": add()}})
    assert exc.value.path == Path.parse("rules[a]")
    assert len(root.rules) == 2


def test_keyfield_needs_a_path():
    with pytest.raises(TypeError):
        _ = KeyField()


class Keyed(VcoModel):
    one: Annotated[list[Rule], KeyField("a")] = Field(default_factory=list)
    two: Annotated[list[Rule], KeyField("a", "b")] = Field(default_factory=list)
    alt: Annotated[list[Rule], KeyField("a", alt="i", label="n")] = Field(
        default_factory=list
    )
    none: list[Rule] = Field(default_factory=list)


def test_key_spec():
    fields = Keyed.model_fields
    assert get_key_spec(fields["one"]) == KeySpec(("a",))
    two_spec = get_key_spec(fields["two"])
    assert two_spec is not None
    assert two_spec.paths == ("a", "b")
    alt_spec = get_key_spec(fields["alt"])
    assert alt_spec is not None
    assert alt_spec.alt == "i"
    assert alt_spec.label == "n"
    assert get_key_spec(fields["none"]) is None
