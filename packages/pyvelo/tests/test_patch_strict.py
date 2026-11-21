"""Strict patches (spec 5.10.2): fields, validation, unknown keys, all or nothing."""

import copy
from typing import Annotated

import pytest
from pydantic import MISSING, ValidationError
from pyvelo.config.resolver import ResolutionError
from pyvelo.core import Alias, PatchError, VcoModel
from pyvelo.core.fields import Resolvable
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
        ({"rules": {"z": {"priority": "abc"}}}, "rules[z]"),
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


def test_defaulting_markers_still_default_on_parse():
    assert Root.model_validate({"safe": "abc"}).safe is False


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"nope": 1}, "nope"),
        ({"sub": {"nope": 1}}, "sub.nope"),
        ({"rules": {"a": {"nope": 1}}}, "rules[a].nope"),
        ({"rules": {"z": {"nope": 1}}}, "rules[z].nope"),
        ({"maybe": {"nope": 1}}, "maybe.nope"),
    ],
)
def test_unknown_key_raises_at_every_depth(patch: dict[str, object], path: str):
    _ = raises_at(Root(rules=[Rule(name="a")]), patch, path)


def test_unknown_key_message_suggests_close_names():
    error = raises_at(Root(), {"cuont": 1}, "cuont")
    assert "Root" in str(error)
    assert "count" in str(error)


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
        "rules": {"a": {"priority": 3}, "z": {"tags": ["t"], "_position": 0}},
        "hops": [{"ip": "10.0.0.0"}],
        "ports": [80, 443],
    }
    before = copy.deepcopy(patch)
    _ = root.merge_patch(patch)
    assert patch == before
