"""Tests for nest_patch: the address of an addressed apply (spec 5.10.1)."""

from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.core import Alias, KeyField, PatchError, VcoModel
from pyvelo.core.merge import nest_patch
from pyvelo.core.rebase import Key, Path


class Leaf(VcoModel):
    x: int = 0


class Mid(VcoModel):
    leaf: Leaf | MISSING = MISSING
    n: int = 0
    dh_group: Annotated[int, Alias("DHGroup")] = 0


class Seg(VcoModel):
    name: str
    mid: Mid | MISSING = MISSING
    rules: list[Leaf] = Field(default_factory=list)


class Outer(VcoModel):
    sub: Mid | MISSING = MISSING
    segments: Annotated[list[Seg], KeyField("name")] = Field(default_factory=list)


class NoSeg(VcoModel):
    n: int = 0


def test_no_address_returns_the_patch():
    assert nest_patch(Outer, {"a": 1}) == {"a": 1}


def test_no_address_needs_a_mapping():
    with pytest.raises(TypeError):
        _ = nest_patch(Outer, 3)
    with pytest.raises(TypeError):
        _ = nest_patch(Outer, None)


def test_path():
    assert nest_patch(Outer, 5, path="sub.n") == {"sub": {"n": 5}}


def test_segment():
    assert nest_patch(Outer, {"x": 1}, segment="S") == {"segments": {"S": {"x": 1}}}


def test_segment_and_path():
    nested = nest_patch(Outer, None, segment="S", path="mid.leaf")
    assert nested == {"segments": {"S": {"mid": {"leaf": None}}}}


def test_part_by_alias_is_nested_by_python_name():
    assert nest_patch(Outer, 2, path="sub.DHGroup") == {"sub": {"dh_group": 2}}
    assert nest_patch(Outer, 2, path="sub.dhGroup") == {"sub": {"dh_group": 2}}


def test_last_part_may_be_a_list():
    nested = nest_patch(Outer, [], segment="S", path="rules")
    assert nested == {"segments": {"S": {"rules": []}}}


def test_unknown_part_names_the_class_the_part_and_the_fields():
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path="nope")
    assert "Outer" in exc.value.message
    assert "'nope'" in exc.value.message
    assert "sub" in exc.value.message
    assert "segments" in exc.value.message
    assert exc.value.path == Path.parse("nope")


def test_unknown_part_after_resolved_ones_is_at_the_walked_path():
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path="sub.nope.x")
    assert "Mid" in exc.value.message
    assert exc.value.path == Path.parse("sub.nope")


def test_unknown_part_under_a_segment_is_at_its_nested_path():
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, segment="S", path="mid.nope")
    assert "Mid" in exc.value.message
    assert exc.value.path == Path(("segments", Key("S"), "mid", "nope"))


@pytest.mark.parametrize(
    ("path", "where"),
    [
        ("segments.x", Path(("segments",))),  # a list before the last part
        ("sub.n.x", Path(("sub", "n"))),  # a scalar before the last part
        ("", Path(("",))),
        ("sub..n", Path(("sub", ""))),
        (".sub", Path(("",))),
    ],
)
def test_bad_addresses(path: str, where: Path):
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path=path)
    assert exc.value.path == where


def test_segment_for_a_class_with_no_segments_field():
    with pytest.raises(PatchError, match="NoSeg has no segments field") as exc:
        _ = nest_patch(NoSeg, {}, segment="S")
    assert exc.value.path == Path(("segments",))
