"""Tests for nest_patch: the address of an addressed apply."""

from types import MappingProxyType
from typing import Annotated, Literal, cast

import pytest
from pydantic import MISSING, Field
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.core import Alias, KeyField, PatchError, VcoModel
from pyvelo.core.merge import address_path, nest_patch
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
        ("sub.n.x", Path(("sub", "n"))),  # a scalar before the last part
        ("", Path(())),
        ("sub..n", Path(())),
        (".sub", Path(())),
    ],
)
def test_bad_addresses(path: str, where: Path) -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path=path)
    assert exc.value.path == where


def test_the_part_after_a_list_names_an_item_of_it() -> None:
    assert nest_patch(Outer, 1, path="segments.x") == {"segments": {"x": 1}}


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


# --- a path through a list's items and a map's entries ----------------------


class _Stop(VcoModel):
    host: str
    port: int
    note: str = ""


class _Hub(VcoModel):
    logical_id: str = ""


class _Net(VcoModel):
    stops: Annotated[list[_Stop], KeyField("host", "port")] = Field(
        default_factory=list
    )
    plain: list[Leaf] = Field(default_factory=list)
    hubs: dict[str, _Hub] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    blobs: list[dict[str, int]] = Field(default_factory=list)


def test_an_item_part_is_the_selector_of_the_item() -> None:
    assert nest_patch(_Net, "x", path='stops["10.0.0.1",514].note') == {
        "stops": {"10.0.0.1,514": {"note": "x"}}
    }
    assert nest_patch(_Net, "x", path="stops[#1].note") == {
        "stops": {"#1": {"note": "x"}}
    }
    assert nest_patch(_Net, 2, path="plain[#0].x") == {"plain": {"#0": {"x": 2}}}
    assert nest_patch(Outer, 1, path="segments[S].mid.dhGroup") == {
        "segments": {"S": {"mid": {"dh_group": 1}}}
    }
    assert nest_patch(Outer, 1, segment="S", path="rules[#0].x") == {
        "segments": {"S": {"rules": {"#0": {"x": 1}}}}
    }
    # A path may end at an item: the patch is then the item's own.
    assert nest_patch(_Net, {"_op": "delete"}, path="stops[#1]") == {
        "stops": {"#1": {"_op": "delete"}}
    }


def test_a_bare_name_after_a_list_is_a_selector_as_written() -> None:
    assert nest_patch(Outer, 1, path="segments.S.mid.n") == {
        "segments": {"S": {"mid": {"n": 1}}}
    }
    # Not a field name: it is not looked up, and not renamed.
    assert nest_patch(Outer, 1, path="segments.dhGroup.mid.dhGroup") == {
        "segments": {"dhGroup": {"mid": {"dh_group": 1}}}
    }


def test_an_entry_of_a_value_list_and_of_a_map_is_named_by_the_next_part() -> None:
    assert nest_patch(_Net, {"_op": "delete"}, path="tags[blue]") == {
        "tags": {"blue": {"_op": "delete"}}
    }
    by_name = nest_patch(_Net, "h", path="hubs.top.logicalId")
    assert by_name == {"hubs": {"top": {"logical_id": "h"}}}
    assert nest_patch(_Net, "h", path="hubs[top].logical_id") == by_name


def test_a_path_object_is_read_as_its_text_is() -> None:
    path = Path(("stops", Key(("10.0.0.1", 514)), "note"))
    assert str(path) == 'stops["10.0.0.1",514].note'
    assert nest_patch(_Net, "x", path=path) == nest_patch(_Net, "x", path=str(path))
    assert nest_patch(_Net, "x", path=path) == {
        "stops": {"10.0.0.1,514": {"note": "x"}}
    }


_AFTER_THE_LIST = "put the item after the list it is in"


@pytest.mark.parametrize(
    ("path", "where", "there", "instead"),
    [
        (
            "sub[x]",
            Path(("sub", Key("x"))),
            "Outer.sub is not a list or a map",
            f"Give a field name after it, or {_AFTER_THE_LIST}",
        ),
        (
            "sub.n[#0]",
            Path(("sub", "n", 0)),
            "Mid.n is not a list or a map",
            f"End the path at it, or {_AFTER_THE_LIST}",
        ),
        (
            "segments[S][#0]",
            Path(("segments", Key("S"), 0)),
            "an item of Outer.segments is not a list or a map",
            f"Give a field name after it, or {_AFTER_THE_LIST}",
        ),
        (
            "[x].sub",
            Path((Key("x"),)),
            "a path starts with a field name",
            f"Start with the field, and {_AFTER_THE_LIST}",
        ),
    ],
)
def test_an_item_part_where_no_list_is_is_refused(
    path: str, where: Path, there: str, instead: str
) -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path=path)
    assert (exc.value.kind, exc.value.path) == ("address", where)
    assert exc.value.path.parts == where.parts
    part = Path((where.parts[-1],))
    assert exc.value.message == f"{there}, so {part} names nothing there. {instead}"


def test_an_item_part_at_the_start_below_a_segment_is_refused() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, segment="S", path="[#0].mid")
    assert (exc.value.kind, exc.value.path) == ("address", "segments[S][#0]")
    assert exc.value.message.startswith(
        "an item of Outer.segments is not a list or a map, so [#0] names nothing"
    )


@pytest.mark.parametrize("path", ["blobs[#0]", "blobs.x", "blobs[#0].y"])
def test_a_list_of_untyped_items_takes_no_item(path: str) -> None:
    part = Path.parse(path).parts[1]
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(_Net, 1, path=path)
    assert (exc.value.kind, exc.value.path) == ("address", Path(("blobs", part)))
    assert exc.value.message == (
        "_Net.blobs is a list of untyped items, which a patch replaces whole, so "
        f"{Path((part,))} names nothing there. End the path at the list and give "
        "a list value"
    )


def test_a_path_cannot_continue_past_an_entry_of_a_value_list() -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(_Net, 1, path="tags[blue].x")
    assert (exc.value.kind, exc.value.path) == ("address", "tags[blue]")
    assert exc.value.message == (
        "an item of _Net.tags does not hold a single sub-model, so the path "
        "cannot continue past it"
    )


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("stops[x", "malformed path 'stops[x' at 7: expected ']'"),
        ("sub..n", "malformed path 'sub..n' at 4: expected a name"),
        (".sub", "malformed path '.sub' at 0: expected a name"),
        ("sub n", "malformed path 'sub n' at 3: expected '.' or '['"),
    ],
)
def test_a_text_that_is_no_path_is_refused_with_the_form(path: str, why: str) -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, 1, path=path)
    assert (exc.value.kind, exc.value.path) == ("address", Path(()))
    assert str(exc.value) == (
        f"the path {path!r} cannot be read: {why}. A path is field names joined "
        "by dots, with an item in brackets after its list, as pyvelo prints it: "
        "segments[Guest].rules[Web].match"
    )
    assert isinstance(exc.value.__cause__, ValueError)


@pytest.mark.parametrize("path", ["", Path(())])
def test_an_empty_path_is_refused(path: str) -> None:
    with pytest.raises(PatchError) as exc:
        _ = nest_patch(Outer, {"sub": {"n": 1}}, segment="S", path=path)
    assert (exc.value.kind, exc.value.path) == ("address", Path(()))
    assert str(exc.value) == (
        "the path is empty, so it names no place and the patch would go to the "
        "whole of what it is sent to. Leave path out to send it there; else "
        "give field names joined by dots, with an item in brackets after its "
        "list, as pyvelo prints it: segments[Guest].rules[Web].match"
    )


# --- a path through a union whose variants name one key differently ---------


class _Den(VcoModel):
    floor_size: int = 0


class _Kennel(VcoModel):
    floor_area: int = 0


class _Cat(VcoModel):
    kind: Literal["cat"] = "cat"
    reach: Annotated[int, Alias("size")] = 0
    den: Annotated[_Den, Alias("home")] = Field(default_factory=_Den)


class _Dog(VcoModel):
    kind: Literal["dog"] = "dog"
    girth: Annotated[int, Alias("size")] = 0
    tail_length: int = 0
    kennel: Annotated[_Kennel, Alias("home")] = Field(default_factory=_Kennel)


class _Zoo(VcoModel):
    animals: list[_Cat | _Dog] = Field(default_factory=list)
    star: _Cat | _Dog | None = None


def test_a_key_the_variants_name_differently_is_nested_as_written() -> None:
    assert nest_patch(_Zoo, 3, path="animals[#0].size") == {
        "animals": {"#0": {"size": 3}}
    }
    assert nest_patch(_Zoo, 3, path="star.size") == {"star": {"size": 3}}
    # Every class that has the key agrees on its name: the python name.
    assert nest_patch(_Zoo, 3, path="animals[#0].tailLength") == {
        "animals": {"#0": {"tail_length": 3}}
    }
    # Below such a key, every class that has it is walked on.
    assert nest_patch(_Zoo, 3, path="animals[#0].home.floorArea") == {
        "animals": {"#0": {"home": {"floor_area": 3}}}
    }
    assert nest_patch(_Zoo, 3, path="star.home.floorSize") == {
        "star": {"home": {"floor_size": 3}}
    }


def test_the_variant_an_item_holds_is_the_one_a_path_reaches() -> None:
    zoo = _Zoo.model_validate(
        {"animals": [{"kind": "dog"}, {"kind": "cat"}], "star": {"kind": "dog"}}
    )
    for path, value in (
        ("animals[#0].size", 3),
        ("animals[#1].size", 4),
        ("animals[#0].home.floorArea", 5),
        ("animals[#1].home.floorSize", 6),
        ("star.size", 7),
    ):
        _ = zoo.merge_patch(nest_patch(_Zoo, value, path=path))
    dog, cat = zoo.animals
    assert isinstance(dog, _Dog)
    assert isinstance(cat, _Cat)
    assert isinstance(zoo.star, _Dog)
    assert (dog.girth, dog.kennel.floor_area) == (3, 5)
    assert (cat.reach, cat.den.floor_size) == (4, 6)
    assert zoo.star.girth == 7
    # A field of the variant the item does not hold is the merge's to refuse.
    with pytest.raises(PatchError) as exc:
        _ = zoo.merge_patch(nest_patch(_Zoo, 1, path="animals[#1].home.floorArea"))
    assert exc.value.kind == "unknown_field"


# --- the whole address as one path, and a segment at its head ---------------


def test_the_whole_address_is_one_path() -> None:
    assert address_path(None, None) == Path(())
    assert address_path(None, "sub.n").parts == ("sub", "n")
    assert address_path("S", None).parts == ("segments", Key("S"))
    assert address_path("S", "mid.leaf") == "segments[S].mid.leaf"
    assert address_path("Corp v2", "rules[#0]").parts == (
        "segments",
        Key("Corp v2"),
        "rules",
        0,
    )
    path = Path(("segments", Key("S"), "mid"))
    assert address_path(None, path) is path
    assert address_path(None, "segments[S].mid") == address_path("S", "mid")


def test_a_segment_at_the_head_of_a_path_is_the_segment_of_the_call() -> None:
    by_path = nest_patch(Outer, 1, path="segments[S].mid.dhGroup")
    assert by_path == nest_patch(Outer, 1, segment="S", path="mid.dhGroup")
    assert by_path == {"segments": {"S": {"mid": {"dh_group": 1}}}}


@pytest.mark.parametrize(
    ("path", "head", "below"),
    [
        ("segments[S].mid.n", "segments[S]", "mid.n"),
        ("segments[T].rules[#0]", "segments[T]", "rules[#0]"),
        ("segments.S.mid", "segments.S", "mid"),
        ("segments[#0]", "segments[#0]", None),
        (Path(("segments", Key("S"), "mid")), "segments[S]", "mid"),
    ],
)
def test_a_segment_given_twice_is_refused(
    path: str, head: str, below: str | None
) -> None:
    instead = (
        "leave out the path"
        if below is None
        else f"start the path below the segment (path={below!r})"
    )
    for call in (
        lambda: address_path("S", path),
        lambda: nest_patch(Outer, {}, segment="S", path=path),
        lambda: nest_patch(NoSeg, {}, segment="S", path=path),
    ):
        with pytest.raises(PatchError) as exc:
            _ = call()
        assert (exc.value.kind, exc.value.path) == ("address", Path(()))
        assert exc.value.message == (
            "the segment is given twice: by segment='S' and by the path, which "
            f"starts at {head}. One of the two would be dropped without a word. "
            f"Give it once: leave out segment, or {instead}"
        )


def test_a_path_that_ends_at_segments_does_not_give_the_segment_twice() -> None:
    with pytest.raises(PatchError, match="Seg has no field 'segments'") as exc:
        _ = nest_patch(Outer, [], segment="S", path="segments")
    assert exc.value.path == Path(("segments", Key("S"), "segments"))


@pytest.mark.parametrize("path", ["stops[x", ""])
def test_an_address_that_cannot_be_read_is_refused_as_nest_patch_refuses_it(
    path: str,
) -> None:
    with pytest.raises(PatchError) as by_address:
        _ = address_path("S", path)
    with pytest.raises(PatchError) as by_nest:
        _ = nest_patch(Outer, 1, segment="S", path=path)
    assert by_address.value.kind == "address"
    assert str(by_address.value) == str(by_nest.value)
