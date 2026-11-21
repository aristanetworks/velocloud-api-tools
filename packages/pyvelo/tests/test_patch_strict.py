"""Strict patches (spec 5.10.2): fields, validation, unknown keys, all or nothing."""

import copy
from enum import IntEnum
from typing import Annotated, Literal

import pytest
from pydantic import MISSING, Discriminator, Field, ValidationError, field_validator
from pyvelo.client.models.configuration.device_settings import NatRule
from pyvelo.config.resolver import ResolutionError
from pyvelo.core import Alias, PatchError, VcoModel, add
from pyvelo.core.fields import KeyField, Resolvable
from pyvelo.core.identity import SegmentIdentity
from pyvelo.core.rebase import Path

from .patch_models import Root, Rule, Sub
from .test_write_safe_resolution import Fake


def raises_at(model: VcoModel, patch: dict[str, object], path: str) -> PatchError:
    with pytest.raises(PatchError) as exc:
        _ = model.merge_patch(patch)
    assert exc.value.path == Path.parse(path)
    return exc.value


def test_patch_error_text():
    assert issubclass(PatchError, ValueError)
    assert str(PatchError("bad", Path.parse("sub.v"))) == "sub.v: bad"
    assert str(PatchError("bad", Path(()))) == "bad"


def test_object_merges_into_a_sub_model():
    root = Root()
    sub = root.sub
    _ = root.merge_patch({"sub": {"v": 3}})
    assert root.sub.v == 3
    assert root.sub is sub


def test_object_builds_an_unset_sub_model():
    root = Root()
    _ = root.merge_patch({"maybe": {"v": 3}})
    assert root.maybe == Sub(v=3)


def test_object_builds_a_sub_model_with_a_required_field():
    root = Root()
    _ = root.merge_patch({"need": {"name": "n"}})
    assert isinstance(root.need, Rule)
    assert root.need.name == "n"


def test_null_unsets_a_field_that_accepts_missing():
    root = Root(maybe=Sub())
    _ = root.merge_patch({"maybe": None})
    assert root.maybe is MISSING


def test_null_sets_none_where_the_type_has_none():
    root = Root(title="t")
    _ = root.merge_patch({"title": None})
    assert root.title is None


@pytest.mark.parametrize("name", ["flag", "sub", "count"])
def test_null_raises_where_the_type_has_neither(name: str):
    _ = raises_at(Root(), {name: None}, name)


def test_value_replaces_and_is_coerced():
    root = Root()
    _ = root.merge_patch({"count": "8443"})
    assert root.count == 8443


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"sub": 3}, "sub"),
        ({"count": "abc"}, "count"),
        ({"title": {"a": 1}}, "title"),
        ({"rules": {"z": add({"priority": "abc"})}}, "rules[z]"),
    ],
)
def test_wrong_type_raises(patch: dict[str, object], path: str):
    error = raises_at(Root(), patch, path)
    assert isinstance(error.__cause__, ValidationError)


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"safe": "abc"}, "safe"),
        ({"hops_safe": [{"ip": "1.1.1.1"}, {"cost": 1}]}, "hops_safe"),
    ],
)
def test_defaulting_markers_do_not_default_in_a_patch(
    patch: dict[str, object], path: str
):
    _ = raises_at(Root(), patch, path)


class Validated(VcoModel):
    prefix: int | None = None
    label: str = ""

    @field_validator("prefix", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v == "" else v

    @field_validator("label")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


def test_field_validators_apply_in_a_patch():
    patch: dict[str, object] = {"prefix": "", "label": "x"}
    model = Validated(prefix=24)
    _ = model.merge_patch(patch)
    parsed = Validated.model_validate(patch)
    assert (model.prefix, model.label) == (parsed.prefix, parsed.label) == (None, "X")

    rule = NatRule.model_validate(
        {
            "type": "source",
            "destCidrPrefix": 24,
            "insideCidrIp": "10.0.0.1",
            "insideCidrPrefix": 32,
            "insideNetmask": "255.255.255.255",
            "outsideCidrIp": "192.0.2.1",
            "outsideCidrPrefix": 32,
            "outsideNetmask": "255.255.255.255",
        }
    )
    _ = rule.merge_patch({"dest_cidr_prefix": ""})
    assert rule.dest_cidr_prefix is None


class Level(IntEnum):
    LOW = 0
    HIGH = 1


class WithEnum(VcoModel):
    level: Level = Level.LOW


def test_enum_field_holds_the_same_type_after_a_patch_as_after_parse():
    parsed = WithEnum.model_validate({"level": 1})
    patched = WithEnum()
    _ = patched.merge_patch({"level": 1})
    assert patched.level == parsed.level
    assert type(patched.level) is type(parsed.level)


def test_defaulting_markers_still_default_on_parse():
    assert Root.model_validate({"safe": "abc"}).safe is False


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"nope": 1}, "nope"),
        ({"sub": {"nope": 1}}, "sub.nope"),
        ({"rules": {"a": {"nope": 1}}}, "rules[a].nope"),
        ({"rules": {"z": add({"nope": 1})}}, "rules[z].nope"),
        ({"maybe": {"nope": 1}}, "maybe.nope"),
    ],
)
def test_unknown_key_raises_at_every_depth(patch: dict[str, object], path: str):
    _ = raises_at(Root(rules=[Rule(name="a")]), patch, path)


def test_unknown_key_message_suggests_close_names():
    error = raises_at(Root(), {"cuont": 1}, "cuont")
    assert "Root" in str(error)
    assert "count" in str(error)


def test_unknown_key_in_a_built_item_names_its_class():
    error = raises_at(
        Root(rules=[Rule(name="a")]),
        {"rules": {"z": add({"priority": 1, "nope": 1})}},
        "rules[z].nope",
    )
    assert "Rule" in str(error)
    error = raises_at(Root(), {"need": {"name": "n", "priorty": 1}}, "need.priorty")
    assert "Rule" in str(error)
    assert "priority" in str(error)


class VariantA(VcoModel):
    kind: Literal["a"] = "a"
    name: str


class VariantB(VcoModel):
    kind: Literal["b"] = "b"
    name: str
    only_b: int = 0


Variant = Annotated[VariantA | VariantB, Discriminator("kind")]


class Wrapper(VcoModel):
    name: str
    inner: Variant | None = None


class Holder(VcoModel):
    items: Annotated[list[Variant], KeyField("name")] = Field(default_factory=list)
    wraps: Annotated[list[Wrapper], KeyField("name")] = Field(default_factory=list)
    one: Variant | None = None


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"items": {"x": add({"kind": "a", "only_b": 5})}}, "items[x].only_b"),
        (
            {"wraps": {"w": add({"inner": {"kind": "a", "name": "i", "only_b": 5}})}},
            "wraps[w].inner.only_b",
        ),
        ({"one": {"kind": "a", "name": "n", "only_b": 5}}, "one.only_b"),
    ],
)
def test_key_of_another_union_variant_raises_in_a_built_item(
    patch: dict[str, object], path: str
):
    error = raises_at(Holder(), patch, path)
    assert "VariantA" in str(error)


class WithAlias(VcoModel):
    dh_group: Annotated[int, Alias("DHGroup")] = 0


@pytest.mark.parametrize(
    ("key", "value"), [("dh_group", 1), ("DHGroup", 2), ("dhGroup", 3)]
)
def test_a_field_is_named_by_python_name_alias_or_camel_case(key: str, value: int):
    model = WithAlias()
    _ = model.merge_patch({key: value})
    assert model.dh_group == value


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"_custom": 1}, "_custom"),
        ({"_op": "add"}, "_op"),
        ({"sub": {"_position": "first"}}, "sub._position"),
    ],
)
def test_directives_raise_outside_a_list_item(patch: dict[str, object], path: str):
    _ = raises_at(Root(), patch, path)


def test_failed_patch_changes_nothing():
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    fields_set = set(root.model_fields_set)
    with pytest.raises(PatchError):
        _ = root.merge_patch({"count": 5, "rules": {"a": {"priority": 9}}, "nope": 1})
    assert root.model_dump() == dump
    assert root.model_fields_set == fields_set


def test_failure_deep_in_a_patch_changes_nothing():
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    fields_set = set(root.model_fields_set)
    with pytest.raises(PatchError):
        _ = root.merge_patch({"count": 5, "rules": {"a": {"priority": 9, "nope": 1}}})
    assert root.model_dump() == dump
    assert root.model_fields_set == fields_set


class Segmented(VcoModel):
    count: int = 0
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0


def test_resolver_error_changes_nothing_and_keeps_its_type():
    model = Segmented()
    dump = model.model_dump()
    with pytest.raises(ResolutionError) as exc:
        _ = model.merge_patch(
            {"count": 5, "segment_id": "No Such Segment"}, resolvers=Fake()
        )
    assert not isinstance(exc.value, PatchError)
    assert model.model_dump() == dump


def test_patch_is_not_mutated():
    root = Root(rules=[Rule(name="a")])
    patch: dict[str, object] = {
        "sub": {"v": 1},
        "maybe": {"v": 2},
        "rules": {"a": {"priority": 3}, "z": add({"tags": ["t"]}, first=True)},
        "hops": [{"ip": "10.0.0.0"}],
        "ports": [80, 443],
    }
    before = copy.deepcopy(patch)
    _ = root.merge_patch(patch)
    assert patch == before
