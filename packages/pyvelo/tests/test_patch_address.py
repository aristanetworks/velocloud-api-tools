"""Tests for nest_patch: the address of an addressed apply."""

from types import MappingProxyType
from typing import Annotated, cast

import pytest
from pydantic import MISSING, Field
from pyvelo.client.models.configuration import DeviceSettingsData
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


def test_no_address_returns_the_patch() -> None:
    assert nest_patch(Outer, {"a": 1}) == {"a": 1}


def test_no_address_needs_a_mapping() -> None:
    with pytest.raises(TypeError):
        _ = nest_patch(Outer, 3)
    with pytest.raises(TypeError):
        _ = nest_patch(Outer, None)


def test_path() -> None:
    assert nest_patch(Outer, 5, path="sub.n") == {"sub": {"n": 5}}


def test_segment() -> None:
    assert nest_patch(Outer, {"x": 1}, segment="S") == {"segments": {"S": {"x": 1}}}


def test_segment_and_path() -> None:
    nested = nest_patch(Outer, None, segment="S", path="mid.leaf")
    assert nested == {"segments": {"S": {"mid": {"leaf": None}}}}


def test_part_by_alias_is_nested_by_python_name() -> None:
    assert nest_patch(Outer, 2, path="sub.DHGroup") == {"sub": {"dh_group": 2}}
    assert nest_patch(Outer, 2, path="sub.dhGroup") == {"sub": {"dh_group": 2}}


def test_last_part_may_be_a_list() -> None:
    nested = nest_patch(Outer, [], segment="S", path="rules")
    assert nested == {"segments": {"S": {"rules": []}}}


def test_unknown_part_names_the_class_the_part_and_the_fields() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path="nope")
    assert "Outer" in exc.value.message
    assert "'nope'" in exc.value.message
    assert "sub" in exc.value.message
    assert "segments" in exc.value.message
    assert exc.value.path == Path.parse("nope")


def test_unknown_part_after_resolved_ones_is_at_the_walked_path() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path="sub.nope.x")
    assert "Mid" in exc.value.message
    assert exc.value.path == Path.parse("sub.nope")


def test_unknown_part_under_a_segment_is_at_its_nested_path() -> None:
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
def test_bad_addresses(path: str, where: Path) -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path=path)
    assert exc.value.path == where


def test_segment_for_a_class_with_no_segments_field() -> None:
    with pytest.raises(PatchError, match="NoSeg has no segments field") as exc:
        _ = nest_patch(NoSeg, {}, segment="S")
    assert exc.value.path == Path(("segments",))


def test_the_nested_patch_is_a_checked_copy() -> None:
    patch: dict[str, object] = {"x": [1]}
    nested = cast(
        "dict[str, dict[str, dict[str, object]]]", nest_patch(Outer, patch, segment="S")
    )
    assert nested == {"segments": {"S": {"x": [1]}}}
    assert nested["segments"]["S"] is not patch
    assert nested["segments"]["S"]["x"] is not patch["x"]
    plain = nest_patch(Outer, patch)
    assert plain == patch
    assert plain is not patch
    assert plain["x"] is not patch["x"]


def test_a_value_that_is_not_json_raises_under_its_address() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, {"x": {1, 2}}, segment="S", path="mid")
    assert exc.value.path == Path(("segments", Key("S"), "mid", "x"))


def test_no_address_needs_a_dict() -> None:
    with pytest.raises(TypeError):
        _ = nest_patch(Outer, MappingProxyType({"sub": {"n": 1}}))


class _Ike(VcoModel):
    lifetime: int = 0


class _Gre(VcoModel):
    redirect: str = ""
    greprop: int = 0


class _Ipsec(VcoModel):
    redirect: str = ""
    ike_proposal: _Ike = Field(default_factory=_Ike)


class _Css(VcoModel):
    config: _Gre | _Ipsec | None = None


class _UnionSegment(VcoModel):
    name: str
    css: _Css = Field(default_factory=_Css)


class _UnionRoot(VcoModel):
    segments: Annotated[list[_UnionSegment], KeyField("name")] = Field(
        default_factory=list
    )
    css: _Css = Field(default_factory=_Css)


def test_a_path_goes_through_a_union_to_a_field_of_a_later_variant() -> None:
    assert nest_patch(_UnionRoot, 5, path="css.config.ikeProposal.lifetime") == {
        "css": {"config": {"ike_proposal": {"lifetime": 5}}}
    }
    assert nest_patch(_UnionRoot, 1, segment="S", path="css.config.greprop") == {
        "segments": {"S": {"css": {"config": {"greprop": 1}}}}
    }
    # A field both variants have is the first variant's name, and both are walked on.
    assert nest_patch(_UnionRoot, "x", path="css.config.redirect") == {
        "css": {"config": {"redirect": "x"}}
    }


def test_a_part_no_variant_has_is_refused_with_every_variant_named() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(_UnionRoot, 1, path="css.config.nope")
    assert exc.value.kind == "address"
    assert str(exc.value.path) == "css.config.nope"
    assert "_Gre | _Ipsec has no field 'nope'" in exc.value.message
    assert exc.value.message.endswith("its fields are redirect, greprop, ike_proposal")

    # Past a field that only one variant has, only that variant is walked.
    with pytest.raises(PatchError, match="_Ike has no field 'greprop'"):
        _ = nest_patch(_UnionRoot, 1, path="css.config.ike_proposal.greprop")
    with pytest.raises(PatchError, match="does not hold a single sub-model"):
        _ = nest_patch(_UnionRoot, 1, path="css.config.greprop.x")


def test_a_css_field_of_an_ipsec_variant_can_be_addressed() -> None:
    nested = nest_patch(
        DeviceSettingsData, 1, segment="Global Segment", path="css.config.ike_proposal"
    )
    assert nested == {
        "segments": {"Global Segment": {"css": {"config": {"ike_proposal": 1}}}}
    }
