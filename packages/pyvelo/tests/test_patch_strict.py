"""Strict patches: fields, validation, unknown keys, all or nothing."""

import copy
import datetime as dt
import ipaddress
import json
import pickle
import uuid
from enum import Enum, IntEnum, StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

import pytest
from pydantic import MISSING, Discriminator, Field, ValidationError, field_validator
from pyvelo.client.models.configuration.device_settings import NatRule
from pyvelo.config.resolver import ResolutionError, ResolverRegistry
from pyvelo.core import Alias, PatchError, VcoModel, add
from pyvelo.core.fields import KeyField, Lenient, NoneIsFalse, Resolvable
from pyvelo.core.identity import SegmentIdentity
from pyvelo.core.rebase import Path

from .patch_models import Hop, Root, Rule, Sub
from .test_write_safe_resolution import Fake


def raises_at(model: VcoModel, patch: dict[str, object], path: str) -> PatchError:
    with pytest.raises(PatchError) as exc:
        _ = model.merge_patch(patch)
    assert exc.value.path == Path.parse(path)
    return exc.value


def test_patch_error_text() -> None:
    assert issubclass(PatchError, ValueError)
    error = PatchError("bad", Path.parse("sub.v"), "invalid_value")
    assert str(error) == "sub.v: bad"
    assert (error.message, error.path, error.kind) == (
        "bad",
        Path.parse("sub.v"),
        "invalid_value",
    )
    assert error.args == ("bad", Path.parse("sub.v"), "invalid_value")
    assert str(PatchError("bad", Path(()), "invalid_value")) == "bad"
    for again in (pickle.loads(pickle.dumps(error)), copy.deepcopy(error)):
        assert type(again) is PatchError
        assert (again.message, again.path, again.kind) == (
            "bad",
            Path.parse("sub.v"),
            "invalid_value",
        )


def test_object_merges_into_a_sub_model() -> None:
    root = Root()
    sub = root.sub
    _ = root.merge_patch({"sub": {"v": 3}})
    assert root.sub.v == 3
    assert root.sub is sub


def test_object_builds_an_unset_sub_model() -> None:
    root = Root()
    _ = root.merge_patch({"maybe": {"v": 3}})
    assert root.maybe == Sub(v=3)


def test_object_builds_a_sub_model_with_a_required_field() -> None:
    root = Root()
    _ = root.merge_patch({"need": {"name": "n"}})
    assert isinstance(root.need, Rule)
    assert root.need.name == "n"


def test_null_unsets_a_field_that_accepts_missing() -> None:
    root = Root(maybe=Sub())
    _ = root.merge_patch({"maybe": None})
    assert root.maybe is MISSING


def test_null_sets_none_where_the_type_has_none() -> None:
    root = Root(title="t")
    _ = root.merge_patch({"title": None})
    assert root.title is None


@pytest.mark.parametrize("name", ["flag", "sub", "count"])
def test_null_raises_where_the_type_has_neither(name: str) -> None:
    error = raises_at(Root(), {name: None}, name)
    assert "a change cannot unset it" in error.message


def test_null_in_a_built_model_where_the_type_has_neither_says_to_leave_the_key_out() -> (
    None
):
    error = raises_at(Root(), {"maybe": {"v": None}}, "maybe.v")
    assert error.message.endswith("leave the key out")


def test_value_replaces_and_is_coerced() -> None:
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
def test_wrong_type_raises(patch: dict[str, object], path: str) -> None:
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
) -> None:
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


def test_field_validators_apply_in_a_patch() -> None:
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


def test_enum_field_holds_the_same_type_after_a_patch_as_after_parse() -> None:
    parsed = WithEnum.model_validate({"level": 1})
    patched = WithEnum()
    _ = patched.merge_patch({"level": 1})
    assert patched.level == parsed.level
    assert type(patched.level) is type(parsed.level)


def test_defaulting_markers_still_default_on_parse() -> None:
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
def test_unknown_key_raises_at_every_depth(patch: dict[str, object], path: str) -> None:
    _ = raises_at(Root(rules=[Rule(name="a")]), patch, path)


def test_unknown_key_message_suggests_close_names() -> None:
    error = raises_at(Root(), {"cuont": 1}, "cuont")
    assert "Root" in str(error)
    assert "count" in str(error)


def test_unknown_key_in_a_built_item_names_its_class() -> None:
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
) -> None:
    error = raises_at(Holder(), patch, path)
    assert "VariantA" in str(error)


class WithAlias(VcoModel):
    dh_group: Annotated[int, Alias("DHGroup")] = 0


@pytest.mark.parametrize(
    ("key", "value"), [("dh_group", 1), ("DHGroup", 2), ("dhGroup", 3)]
)
def test_a_field_is_named_by_python_name_alias_or_camel_case(
    key: str, value: int
) -> None:
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
def test_directives_raise_outside_a_list_item(
    patch: dict[str, object], path: str
) -> None:
    _ = raises_at(Root(), patch, path)


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"rules": {"a": {"rule_id": "x", "ruleId": "y"}}}, "rules[a].rule_id"),
        ({"rules": {"b": add({"rule_id": "x", "ruleId": "y"})}}, "rules[b].rule_id"),
        ({"need": {"name": "n", "rule_id": "x", "ruleId": "y"}}, "need.rule_id"),
    ],
    ids=["merged", "built-item", "built-sub-model"],
)
def test_one_field_named_twice_raises(patch: dict[str, object], path: str) -> None:
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    error = raises_at(root, patch, path)
    assert "'rule_id'" in error.message
    assert "'ruleId'" in error.message
    assert root.model_dump() == dump


def test_failed_patch_changes_nothing() -> None:
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    fields_set = set(root.model_fields_set)
    with pytest.raises(PatchError):
        _ = root.merge_patch({"count": 5, "rules": {"a": {"priority": 9}}, "nope": 1})
    assert root.model_dump() == dump
    assert root.model_fields_set == fields_set


def test_failure_deep_in_a_patch_changes_nothing() -> None:
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    fields_set = set(root.model_fields_set)
    with pytest.raises(PatchError):
        _ = root.merge_patch({"count": 5, "rules": {"a": {"priority": 9, "nope": 1}}})
    assert root.model_dump() == dump
    assert root.model_fields_set == fields_set


class Segmented(VcoModel):
    count: int = 0
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0


def test_resolver_error_changes_nothing_and_keeps_its_type() -> None:
    model = Segmented()
    dump = model.model_dump()
    with pytest.raises(ResolutionError) as exc:
        _ = model.merge_patch(
            {"count": 5, "segment_id": "No Such Segment"}, resolvers=Fake()
        )
    assert not isinstance(exc.value, PatchError)
    assert model.model_dump() == dump


class Bound(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0
    segment_ref: Annotated[str, Resolvable(SegmentIdentity, lambda i: i.name)] = ""


class Either(VcoModel):
    segment_id: Annotated[
        int | str, Resolvable(SegmentIdentity, lambda i: i.segment_id)
    ] = 0


class Member(VcoModel):
    name: str
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)]


class Members(VcoModel):
    members: Annotated[list[Member], KeyField("name")] = Field(default_factory=list)


def test_a_name_with_no_resolver_registered_is_refused_at_its_field() -> None:
    empty = ResolverRegistry()
    with pytest.raises(PatchError) as exc:
        _ = Bound().merge_patch({"segment_id": "Guest"}, resolvers=empty)
    assert exc.value.path == Path.parse("segment_id")
    assert "'Guest'" in exc.value.message
    assert "SegmentIdentity" in exc.value.message
    assert isinstance(exc.value.__cause__, LookupError)
    assert not isinstance(exc.value.__cause__, ResolutionError)
    with pytest.raises(PatchError) as exc:
        _ = Members().merge_patch(
            {"members": {"m": add({"segment_id": "Guest"})}}, resolvers=empty
        )
    assert exc.value.path == Path.parse("members[m].segment_id")
    assert "SegmentIdentity" in exc.value.message


def test_an_id_needs_no_resolver() -> None:
    empty = ResolverRegistry()
    model = Bound()
    _ = model.merge_patch(
        {"segment_id": "7", "segment_ref": "any text"}, resolvers=empty
    )
    assert (model.segment_id, model.segment_ref) == (7, "any text")
    members = Members()
    _ = members.merge_patch({"members": {"m": add({"segment_id": 7})}}, resolvers=empty)
    assert members.members[0].segment_id == 7


def test_patch_is_not_mutated() -> None:
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


@pytest.mark.parametrize(
    ("patch", "path", "text"),
    [
        ({"rules": (r for r in [{"name": "x"}])}, "rules", "generator"),
        ({"rules": reversed([{"name": "x"}])}, "rules", "list_reverseiterator"),
        ({"rules": ({"name": "x"},)}, "rules", "tuple"),
        ({"names": {"a", "b"}}, "names", "set"),
        ({"sub": MappingProxyType({"v": 1})}, "sub", "mappingproxy"),
        ({"title": uuid.UUID(int=1)}, "title", "UUID"),
        ({"blobs": [{"a": float("nan")}]}, "blobs[#0].a", "nan"),
        ({"count": float("inf")}, "count", "inf"),
        ({"rules": {True: {"priority": 1}}}, "rules", "True"),
        ({"rules": {None: {"priority": 1}}}, "rules", "None"),
    ],
)
def test_a_value_that_is_not_json_raises(
    patch: dict[str, object], path: str, text: str
) -> None:
    root = Root(rules=[Rule(name="a")], names=["a"])
    dump = copy.deepcopy(root.model_dump())
    error = raises_at(root, patch, path)
    assert text in error.message
    assert root.model_dump() == dump


def nested(levels: int) -> object:
    return json.loads('{"x":' * levels + "1" + "}" * levels)


def test_nesting_is_bounded() -> None:
    # The patch, the list and 62 objects are levels 0 to 63; one more is too deep.
    root = Root()
    _ = root.merge_patch({"blobs": [nested(62)]})
    assert root.blobs == [nested(62)]
    with pytest.raises(PatchError) as exc:
        _ = Root().merge_patch({"blobs": [nested(63)]})
    assert "64" in exc.value.message
    assert "split it into smaller patches" in exc.value.message
    assert "segment or path" in exc.value.message
    assert len(exc.value.path.parts) == 64


def test_the_model_shares_no_object_with_the_patch() -> None:
    patch: dict[str, object] = {"blobs": [{"who": {"tags": ["x"]}}]}
    first, second = Root(), Root()
    _ = first.merge_patch(patch)
    _ = second.merge_patch(patch)
    first.blobs[0]["who"]["tags"].append("edited")
    assert second.blobs == [{"who": {"tags": ["x"]}}]
    assert patch == {"blobs": [{"who": {"tags": ["x"]}}]}


def test_a_patch_that_is_not_a_dict_is_a_type_error() -> None:
    root = Root()
    with pytest.raises(TypeError, match=r"write .*<field>.*: value"):
        _ = root.merge_patch(MappingProxyType({"count": 1}))
    with pytest.raises(TypeError, match="dict"):
        _ = root.merge_patch(json.loads('[["count", 1]]'))
    assert root.count == 0


class Colour(Enum):
    RED = "red"


@pytest.mark.parametrize(
    ("value", "remedy"),
    [
        (uuid.UUID(int=1), "str(value)"),
        (Colour.RED, "member.value"),
        (dt.date(2026, 1, 1), "isoformat()"),
        (dt.time(1, 2), "isoformat()"),
        ({"a"}, "as a list"),
        (("a",), "as a list"),
        ((x for x in "a"), "as a list"),
    ],
)
def test_a_value_that_is_not_json_says_what_to_write_instead(
    value: object, remedy: str
) -> None:
    error = raises_at(Root(), {"blobs": [{"a": value}]}, "blobs[#0].a")
    assert remedy in error.message


@pytest.mark.parametrize("value", [uuid.UUID(int=1), Colour.RED])
def test_a_uuid_or_enum_member_points_to_the_json_dump(value: object) -> None:
    error = raises_at(Root(), {"title": value}, "title")
    assert 'model_dump(mode="json")' in error.message


@pytest.mark.parametrize(
    ("patch", "path", "text"),
    [
        ({"sub": Sub(v=1)}, "sub", "Sub"),
        ({"rules": [Rule(name="x")]}, "rules[#0]", "Rule"),
        ({"maybe": MISSING}, "maybe", "MISSING"),
        ({"title": Colour.RED}, "title", "Colour"),
        ({"title": ipaddress.IPv4Address("10.0.0.1")}, "title", "IPv4Address"),
        ({"title": dt.datetime(2026, 1, 1)}, "title", "datetime"),
    ],
)
def test_a_python_object_is_not_a_json_value(
    patch: dict[str, object], path: str, text: str
) -> None:
    root = Root(rules=[Rule(name="a")], maybe=Sub())
    dump = copy.deepcopy(root.model_dump())
    error = raises_at(root, patch, path)
    assert error.message.startswith(f"{text} is not a JSON value")
    assert root.model_dump() == dump


class Count(IntEnum):
    ONE = 1


class Word(StrEnum):
    HELLO = "hello"


class Ratio(float):
    pass


def test_a_subclass_of_a_json_scalar_is_stored_as_the_plain_value() -> None:
    root = Root()
    _ = root.merge_patch(
        {
            "blobs": [
                {"n": Count.ONE, "w": Word.HELLO, "r": Ratio(0.5), "k": [Count.ONE]}
            ]
        }
    )
    (blob,) = root.blobs
    assert blob == {"n": 1, "w": "hello", "r": 0.5, "k": [1]}
    assert {key: type(value) for key, value in blob.items() if key != "k"} == {
        "n": int,
        "w": str,
        "r": float,
    }
    assert type(blob["k"][0]) is int


def test_a_subclass_of_str_as_a_key_is_stored_as_the_plain_key() -> None:
    root = Root()
    _ = root.merge_patch({"blobs": [{"inner": {Word.HELLO: 1}}]})
    (blob,) = root.blobs
    (key,) = blob["inner"]
    assert key == "hello"
    assert type(key) is str


def test_from_python_a_patch_is_json_too() -> None:
    root = Root(rules=[Rule(name="a")], maybe=Sub(v=1))
    _ = root.merge_patch(
        {"maybe": None, "sub": Sub(v=2).model_dump(mode="json"), "title": "red"}
    )
    assert root.maybe is MISSING
    assert root.sub == Sub(v=2)


class Net(VcoModel):
    name: str
    advertise: Annotated[bool, NoneIsFalse] = True
    lease: Annotated[int, Lenient(lambda v: 86400 if v is None else v)] = 86400
    passive: Annotated[bool, Lenient(lambda v: True if v in (None, "") else v)] = True
    note: str | None = "n"
    opt: int | MISSING = 5
    both: int | None | MISSING = MISSING


class Nets(VcoModel):
    nets: Annotated[list[Net], KeyField("name")] = Field(default_factory=list)
    one: Net | MISSING = MISSING


def test_tolerant_fields_still_tolerate_on_parse() -> None:
    net = Net.model_validate(
        {"name": "n", "advertise": None, "lease": None, "passive": ""}
    )
    assert (net.advertise, net.lease, net.passive) == (False, 86400, True)


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"nets": {"new": add({"advertise": None})}}, "nets[new].advertise"),
        ({"nets": {"new": add({"lease": None})}}, "nets[new].lease"),
        ({"nets": [{"name": "new", "advertise": None}]}, "nets[new].advertise"),
        ({"one": {"name": "new", "advertise": None}}, "one.advertise"),
        ({"nets": {"corp": {"passive": ""}}}, "nets[corp].passive"),
        ({"nets": {"new": add({"passive": ""})}}, "nets[new]"),
        ({"nets": [{"name": "new", "passive": ""}]}, "nets"),
    ],
)
def test_null_and_empty_text_are_not_coerced_in_a_patch(
    patch: dict[str, object], path: str
) -> None:
    model = Nets(nets=[Net(name="corp")])
    _ = raises_at(model, patch, path)
    assert [(n.name, n.advertise, n.passive) for n in model.nets] == [
        ("corp", True, True)
    ]
    assert model.one is MISSING


def test_null_in_a_built_item_follows_the_null_rule() -> None:
    model = Nets()
    _ = model.merge_patch(
        {"nets": {"new": add({"note": None, "opt": None, "both": None})}}
    )
    new = model.nets[0]
    assert new.note is None
    assert new.opt is MISSING
    # A field that takes both is None in a built item, as a dump writes it ...
    assert new.both is None
    # ... and unset by a change, where leaving the key out would change nothing.
    _ = model.merge_patch({"nets": {"new": {"both": None}}})
    assert new.both is MISSING


class Leaf(VcoModel):
    both: int | None | MISSING = MISSING


class Loose(VcoModel):
    both: int | None | MISSING = MISSING
    inner: Leaf | MISSING = MISSING


class LooseHolder(VcoModel):
    sub: Loose | MISSING = MISSING
    by_name: dict[str, Loose] = Field(default_factory=dict)


def test_null_in_a_sub_model_the_patch_builds_follows_the_null_rule() -> None:
    holder = LooseHolder()
    _ = holder.merge_patch({"sub": {"both": None}})
    assert isinstance(holder.sub, Loose)
    assert holder.sub.both is None
    # A change to the sub-model that now exists unsets the field.
    _ = holder.merge_patch({"sub": {"both": None}})
    assert holder.sub.both is MISSING


def test_null_in_a_map_entry_the_patch_builds_follows_the_null_rule() -> None:
    holder = LooseHolder()
    _ = holder.merge_patch({"by_name": {"a": add({"both": None})}})
    assert holder.by_name["a"].both is None
    _ = holder.merge_patch({"by_name": {"a": {"both": None}}})
    assert holder.by_name["a"].both is MISSING


def test_null_in_a_sub_model_inside_a_built_sub_model_follows_the_null_rule() -> None:
    holder = LooseHolder()
    _ = holder.merge_patch({"sub": {"inner": {"both": None}}})
    assert isinstance(holder.sub, Loose)
    assert isinstance(holder.sub.inner, Leaf)
    assert holder.sub.inner.both is None
    _ = holder.merge_patch({"sub": {"inner": {"both": None}}})
    assert isinstance(holder.sub.inner, Leaf)
    assert holder.sub.inner.both is MISSING


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"count": True}, "count"),
        ({"rules": {"a": {"priority": False}}}, "rules[a].priority"),
        ({"rules": {"z": add({"priority": True})}}, "rules[z].priority"),
        ({"rules": [{"name": "x", "priority": True}]}, "rules[x].priority"),
        ({"ports": [80, True]}, "ports[#1]"),
        ({"need": {"name": "n", "priority": True}}, "need.priority"),
        ({"need": {"name": "n", "sub": {"v": True}}}, "need.sub.v"),
        (
            {"routes": {"+": add({"dest": "d", "prefix": True, "gw": "g"})}},
            'routes["+"].prefix',
        ),
    ],
)
def test_a_boolean_is_not_taken_for_a_number(
    patch: dict[str, object], path: str
) -> None:
    root = Root(rules=[Rule(name="a")])
    dump = copy.deepcopy(root.model_dump())
    error = raises_at(root, patch, path)
    assert "true" in error.message or "false" in error.message
    assert error.message.endswith("send a number")
    assert root.model_dump() == dump


def test_a_boolean_is_taken_where_the_field_holds_one() -> None:
    root = Root()
    _ = root.merge_patch({"flag": True, "safe": True, "blobs": [{"a": True}]})
    assert (root.flag, root.safe, root.blobs) == (True, True, [{"a": True}])


def test_a_field_whose_id_can_be_text_takes_the_name_as_it_is_with_no_resolver() -> (
    None
):
    model = Either()
    _ = model.merge_patch({"segment_id": "Guest"}, resolvers=ResolverRegistry())
    assert model.segment_id == "Guest"


class Tunnel(VcoModel):
    name: str
    dh_group: Annotated[int, Alias("DHGroup")] = 0
    peer_address: str
    next_hops: list[Hop] = Field(default_factory=list)


class Tunnels(VcoModel):
    tunnels: Annotated[list[Tunnel], KeyField("name")] = Field(default_factory=list)
    main: Tunnel | MISSING = MISSING
    spares: dict[str, Tunnel] = Field(default_factory=dict)


BAD_TUNNEL: dict[str, object] = {"dh_group": "x", "next_hops": [{"cost": "y"}]}
INT = "Input should be a valid integer, unable to parse string as an integer"


def test_validation_messages_name_fields_as_a_patch_writes_them() -> None:
    error = raises_at(Tunnels(), {"tunnels": {"t": add(BAD_TUNNEL)}}, "tunnels[t]")
    assert error.message == (
        f"invalid value: dh_group: {INT}; peer_address: Field required; "
        f"next_hops[#0].ip: Field required; next_hops[#0].cost: {INT}"
    )
    error = raises_at(Tunnels(), {"main": {"name": "m", **BAD_TUNNEL}}, "main")
    assert f"dh_group: {INT}; peer_address: Field required" in error.message
    error = raises_at(
        Tunnels(), {"tunnels": [{"name": "t", "peerAddress": 5}]}, "tunnels"
    )
    assert error.message == (
        "invalid value: [#0].peer_address: Input should be a valid string"
    )
    error = raises_at(
        Tunnels(), {"spares": {"s": add({"name": "s", "peerAddress": 5})}}, "spares[s]"
    )
    assert (
        error.message == "invalid value: peer_address: Input should be a valid string"
    )


def test_a_field_value_has_no_name_in_its_message() -> None:
    error = raises_at(Root(), {"count": "abc"}, "count")
    assert error.message == f"invalid value: {INT}"
