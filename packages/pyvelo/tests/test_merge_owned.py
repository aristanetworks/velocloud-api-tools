"""``merge_patch`` on an edge-layer ``Inheritable``, and on the profile layer's.

On the edge layer a patch changes who owns a value: ``null`` inherits, a scalar
sets it local, an object merges into the local value or, when the value is
inherited, into a copy of the profile's. A member of a coupled group localizes
the group first. The profile layer keeps the plain rules of a field.
"""

from typing import Annotated, Any

import pytest
from layered_models import Mod, mod_json
from pydantic import MISSING, Field
from pyvelo.client.exceptions import UnboundError
from pyvelo.core import (
    ByPresence,
    Default,
    Edge,
    Inheritable,
    KeyField,
    Layered,
    OverrideFlag,
    PatchError,
    Profile,
    Unconverted,
    VcoModel,
    bind_layers,
    kind_default,
)
from pyvelo.core.layers import L

_PLAIN = {"legacy", "edge_note", "profile_note"}

_SEGMENT = {"segmentId": 1, "name": "S"}
_PROFILE = mod_json(
    enabled=False,
    settings={"a": 4, "b": True},
    pairA={"a": 2},
    pairB={"a": 3},
    pairOn=True,
    segments=[{"segment": _SEGMENT, "mapping": {"override": True, "value": 3}}],
)


def _dump(model: Mod[Edge] | Mod[Profile]) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude=_PLAIN)


def _bound(**edge: object) -> tuple[Mod[Edge], Mod[Profile]]:
    """An edge with *edge* as its wire keys, bound to the profile ``_PROFILE``."""
    keys: dict[str, object] = {"segments": [{"segment": _SEGMENT}], **edge}
    e = Mod[Edge].model_validate(mod_json(**keys))
    p = Mod[Profile].model_validate(_PROFILE)
    bind_layers(e, p)
    return e, p


_LOCAL_PAIR = {
    "pairA": {"a": 1},
    "pairB": {"a": 1},
    "pairOn": False,
}


# ---------------------------------------------------------------------------
# The rows of the table
# ---------------------------------------------------------------------------


def test_null_inherits() -> None:
    e, _ = _bound(enabled=False, level=3, settings={"a": 9})
    _ = e.merge_patch({"enabled": None, "level": None, "settings": None})
    assert e.enabled.stored is MISSING
    assert e.level.stored is MISSING
    assert e.settings.stored is MISSING
    assert {"enabled", "level", "settings"}.isdisjoint(_dump(e))
    # What it inherits is the profile's.
    assert e.enabled.localize() is False


def test_null_inherits_a_by_flag_field_as_its_reset_object() -> None:
    e, _ = _bound(affinity={"override": True, "affinity": ["x"]})
    _ = e.merge_patch({"affinity": None})
    assert not e.affinity.is_local
    assert _dump(e)["affinity"] == {"override": False, "affinity": []}


def test_null_on_a_member_inherits_the_group() -> None:
    e, _ = _bound(**_LOCAL_PAIR)
    assert e.pair.is_local
    _ = e.merge_patch({"pair_b": None})
    assert not e.pair.is_local
    assert {"pairA", "pairB", "pairOn"}.isdisjoint(_dump(e))
    assert e.pair_a.stored is MISSING
    assert e.pair_on.stored is MISSING


def test_a_scalar_sets_local() -> None:
    e, _ = _bound()
    _ = e.merge_patch({"enabled": True, "level": 4})
    assert e.enabled.is_local
    assert e.enabled.local is True
    assert e.level.local == 4
    dump = _dump(e)
    assert dump["enabled"] is True
    assert dump["level"] == 4
    # A value equal to what the profile gives is the edge's own all the same.
    _ = e.merge_patch({"enabled": False})
    assert _dump(e)["enabled"] is False


def test_a_scalar_that_validation_refuses_changes_nothing() -> None:
    e, _ = _bound(level=3)
    before = _dump(e)
    with pytest.raises(PatchError) as exc:
        _ = e.merge_patch({"level": "abc"})
    assert exc.value.kind == "invalid_value"
    assert str(exc.value.path) == "level"
    assert _dump(e) == before


def test_a_scalar_that_fails_in_a_segment_is_reported_where_it_sits() -> None:
    e, _ = _bound()
    with pytest.raises(PatchError) as exc:
        _ = e.merge_patch({"segments": {"S": {"mapping": 5}}})
    assert exc.value.path.parts[-1] == "mapping"
    assert "S" in str(exc.value.path)


def test_an_object_merges_into_a_local_value() -> None:
    e, p = _bound(settings={"a": 1, "b": False})
    _ = e.merge_patch({"settings": {"a": 9}})
    assert _dump(e)["settings"] == {"a": 9, "b": False}
    assert p.settings.a == 4  # the profile is not touched


def test_an_object_on_an_inherited_field_starts_from_the_profile() -> None:
    e, p = _bound()
    _ = e.merge_patch({"settings": {"a": 7}})
    assert e.settings.is_local
    assert _dump(e)["settings"] == {"a": 7, "b": True}
    assert (p.settings.a, p.settings.b) == (4, True)


def test_an_object_on_an_inherited_field_the_profile_lacks_starts_from_the_display_default() -> (
    None
):
    e = Mod[Edge].model_validate(mod_json())
    p = Mod[Profile].model_validate(mod_json())
    bind_layers(e, p)
    _ = e.merge_patch({"settings": {"a": 7}})
    assert _dump(e)["settings"] == {"a": 7, "b": False}
    assert "settings" not in p.model_fields_set


def test_a_value_for_a_member_of_an_inherited_group_localizes_the_group() -> None:
    e, _ = _bound()
    assert not e.pair.is_local
    _ = e.merge_patch({"pair_on": False})
    assert e.pair.is_local
    dump = _dump(e)
    assert dump["pairOn"] is False
    assert dump["pairA"]["a"] == 2
    assert dump["pairB"]["a"] == 3


def test_an_object_for_a_member_of_an_inherited_group_merges_into_its_copy() -> None:
    e, _ = _bound()
    _ = e.merge_patch({"pair_b": {"a": 8}})
    dump = _dump(e)
    assert dump["pairB"] == {"a": 8, "b": False}
    assert dump["pairA"]["a"] == 2
    assert dump["pairOn"] is True


def test_a_value_for_a_member_of_a_local_group_changes_that_member() -> None:
    e, _ = _bound(**_LOCAL_PAIR)
    _ = e.merge_patch({"pair_on": True})
    dump = _dump(e)
    assert dump["pairOn"] is True
    assert dump["pairA"]["a"] == dump["pairB"]["a"] == 1


def test_a_whole_group_in_one_patch() -> None:
    e, _ = _bound()
    _ = e.merge_patch(
        {"pair_a": {"a": 5}, "pair_b": {"a": 6}, "pair_on": False},
    )
    dump = _dump(e)
    assert (dump["pairA"]["a"], dump["pairB"]["a"], dump["pairOn"]) == (5, 6, False)


# ---------------------------------------------------------------------------
# The flag of a ByFlag field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("local", [True, False])
def test_the_flag_of_a_by_flag_field_is_not_patchable(flag: bool, local: bool) -> None:
    keys: dict[str, object] = (
        {"segments": [{"segment": _SEGMENT, "mapping": {"override": True}}]}
        if local
        else {}
    )
    e, _ = _bound(**keys)
    before = _dump(e)
    with pytest.raises(PatchError, match="null") as exc:
        _ = e.merge_patch({"segments": {"S": {"mapping": {"override": flag}}}})
    assert exc.value.path.parts[-2:] == ("mapping", "override")
    assert "set_local()" in str(exc.value)
    assert 'send {"segments": {"S": {"mapping": null}}}' in str(exc.value)
    assert _dump(e) == before


def test_the_flag_is_refused_beside_a_value_too() -> None:
    e, _ = _bound(affinity={"override": True})
    with pytest.raises(PatchError, match="null") as exc:
        _ = e.merge_patch({"affinity": {"override": False, "affinity": ["a"]}})
    assert 'send {"affinity": null}' in str(exc.value)
    assert _dump(e)["affinity"] == {"override": True, "affinity": []}


def test_an_object_on_an_inherited_by_flag_field_localizes_it() -> None:
    e, _ = _bound()
    _ = e.merge_patch({"segments": {"S": {"mapping": {"value": 5}}}})
    mapping = _dump(e)["segments"][0]["mapping"]
    assert mapping["override"] is True
    assert mapping["value"] == 5


def test_an_object_on_a_local_by_flag_field_merges_into_it() -> None:
    e, _ = _bound(affinity={"override": True, "affinity": ["x"]})
    _ = e.merge_patch({"affinity": {"affinity": {"y": {"_op": "add"}}}})
    assert _dump(e)["affinity"] == {"override": True, "affinity": ["x", "y"]}


def test_an_object_on_a_by_flag_field_that_is_stored_unflagged_starts_from_the_profile() -> (
    None
):
    # The edge stores a reset object (flag false): not local, so the profile's value
    # is copied in and the patch merges into it.
    e, _ = _bound(affinity={"override": False, "affinity": []})
    assert not e.affinity.is_local
    _ = e.merge_patch({"affinity": {"affinity": {"z": {"_op": "add"}}}})
    assert e.affinity.is_local
    assert _dump(e)["affinity"] == {"override": True, "affinity": ["z"]}


# ---------------------------------------------------------------------------
# The profile layer
# ---------------------------------------------------------------------------


class _Nullable(Layered[L]):
    plain: Annotated[Inheritable[bool], ByPresence(), Default(False)] = Field(
        default=...
    )
    optional: Annotated[Inheritable[bool | None], ByPresence(), Default(None)] = Field(
        default=...
    )


def test_profile_null_rows_follow_the_inner_type() -> None:
    profile = _Nullable[Profile].model_validate({"plain": True, "optional": True})
    with pytest.raises(PatchError) as exc:
        _ = profile.merge_patch({"plain": None})
    assert exc.value.kind == "null"
    assert profile.plain is True
    _ = profile.merge_patch({"optional": None})
    assert profile.optional is None
    assert "optional" in profile.model_fields_set
    assert profile.model_dump(mode="json", by_alias=True)["optional"] is None


def test_null_inherits_where_the_edge_takes_none_too() -> None:
    # On the edge layer null is inherit whatever the inner type takes.
    edge = _Nullable[Edge].model_validate({"plain": True, "optional": True})
    _ = edge.merge_patch({"plain": None, "optional": None})
    assert edge.plain.stored is MISSING
    assert edge.optional.stored is MISSING


def test_a_scalar_patch_on_the_profile_assigns_it() -> None:
    profile = Mod[Profile].model_validate(mod_json())
    _ = profile.merge_patch({"enabled": True})  # the display default, stored
    assert profile.model_dump(mode="json", by_alias=True)["enabled"] is True


# ---------------------------------------------------------------------------
# All or nothing
# ---------------------------------------------------------------------------


def test_a_failed_patch_leaves_a_localized_group_inherited() -> None:
    e, _ = _bound()
    before = _dump(e)
    with pytest.raises(PatchError):
        _ = e.merge_patch({"pair_on": False, "level": "abc"})
    assert _dump(e) == before
    assert not e.pair.is_local
    assert e.pair_a.stored is MISSING
    # The same patch without the failing key localizes the group, in the model itself.
    _ = e.merge_patch({"pair_on": False})
    assert e.pair.is_local


def test_a_failed_patch_leaves_a_localized_value_inherited() -> None:
    e, _ = _bound()
    before = _dump(e)
    with pytest.raises(PatchError):
        _ = e.merge_patch({"settings": {"a": 7}, "level": "abc"})
    assert _dump(e) == before
    assert not e.settings.is_local


def test_a_failed_patch_leaves_an_inherited_by_flag_field_inherited() -> None:
    e, _ = _bound()
    before = _dump(e)
    with pytest.raises(PatchError):
        _ = e.merge_patch(
            {"segments": {"S": {"mapping": {"value": 5}}}, "level": "abc"}
        )
    assert _dump(e) == before
    assert not e.segments[0].mapping.is_local


def test_a_patch_on_an_unbound_edge_that_needs_localize_raises_unbound() -> None:
    e = Mod[Edge].model_validate(mod_json())
    before = _dump(e)
    with pytest.raises(UnboundError, match="loaded state"):
        _ = e.merge_patch({"settings": {"a": 7}})
    with pytest.raises(UnboundError, match="loaded state"):
        _ = e.merge_patch({"pair_on": False})
    assert _dump(e) == before
    # A value that copies nothing needs no profile.
    _ = e.merge_patch({"level": 4, "enabled": None, "settings": None})
    assert e.level.local == 4


# ---------------------------------------------------------------------------
# A list in an Inheritable
# ---------------------------------------------------------------------------


class _Item(VcoModel):
    name: str
    value: int = 0


class _Listed(Layered[L]):
    items: Annotated[
        Inheritable[list[_Item]],
        ByPresence(),
        KeyField("name"),
        Default(factory=lambda: [_Item(name="a")]),
    ] = Field(default=...)


def _listed(edge: object, profile: object) -> tuple[_Listed[Edge], _Listed[Profile]]:
    e = _Listed[Edge].model_validate(edge)
    p = _Listed[Profile].model_validate(profile)
    bind_layers(e, p)
    return e, p


def test_item_operations_on_an_inherited_list_start_from_the_profile() -> None:
    e, p = _listed({}, {"items": [{"name": "a", "value": 2}, {"name": "b"}]})
    _ = e.merge_patch({"items": {"a": {"value": 5}, "c": {"_op": "add"}}})
    assert e.items.is_local
    assert e.model_dump(mode="json", by_alias=True)["items"] == [
        {"name": "a", "value": 5},
        {"name": "b", "value": 0},
        {"name": "c", "value": 0},
    ]
    assert [i.value for i in p.items] == [2, 0]


def test_a_list_value_on_an_edge_list_sets_it_local() -> None:
    e, _ = _listed({}, {"items": [{"name": "a"}]})
    _ = e.merge_patch({"items": [{"name": "z", "value": 1}]})
    assert e.model_dump(mode="json", by_alias=True)["items"] == [
        {"name": "z", "value": 1}
    ]
    _ = e.merge_patch({"items": None})
    assert not e.items.is_local


@pytest.mark.parametrize("patch", [{"a": {"value": 0}}, {"a": {}}])
def test_a_patch_on_a_profile_list_marks_it_stored_even_when_it_ends_as_the_default(
    patch: dict[str, object],
) -> None:
    profile = _Listed[Profile].model_validate({})
    assert "items" not in profile.model_fields_set
    assert "items" not in profile.model_dump(mode="json", by_alias=True)
    _ = profile.merge_patch({"items": patch})
    assert "items" in profile.model_fields_set
    assert profile.model_dump(mode="json", by_alias=True)["items"] == [
        {"name": "a", "value": 0}
    ]


def test_a_failed_patch_on_a_profile_list_does_not_mark_it_stored() -> None:
    profile = _Listed[Profile].model_validate({})
    with pytest.raises(PatchError):
        _ = profile.merge_patch({"items": {"nope": {"value": 1}}})
    assert "items" not in profile.model_fields_set


# ---------------------------------------------------------------------------
# A sibling OverrideFlag
# ---------------------------------------------------------------------------


class _Flagged(Layered[L]):
    flag: Annotated[bool, OverrideFlag] = False
    x: Annotated[Inheritable[int], ByPresence(), Default(0)] = Field(default=...)
    plain: int = 0
    legacy: Unconverted[int] = kind_default(0)


def test_a_change_to_an_inheritable_does_not_turn_on_an_override_flag() -> None:
    edge = _Flagged[Edge].model_validate({"x": 1})
    _ = edge.merge_patch({"x": 3})
    assert edge.flag is False
    profile = _Flagged[Profile].model_validate({"x": 1})
    _ = profile.merge_patch({"x": 3})
    assert profile.flag is False


def test_inheriting_does_not_turn_on_an_override_flag() -> None:
    model = _Flagged[Edge].model_validate({"x": 1})
    _ = model.merge_patch({"x": None})
    assert model.x.stored is MISSING
    assert model.flag is False


def test_a_structural_or_unconverted_change_still_turns_on_the_override_flag() -> None:
    for patch in ({"plain": 2}, {"legacy": 2}):
        edge = _Flagged[Edge].model_validate({"x": 1})
        _ = edge.merge_patch(patch)
        assert edge.flag is True
        profile = _Flagged[Profile].model_validate({"x": 1})
        _ = profile.merge_patch(patch)
        assert profile.flag is True


def test_an_inheritable_change_beside_a_plain_one_leaves_the_flag_to_the_plain_one() -> (
    None
):
    model = _Flagged[Edge].model_validate({})
    _ = model.merge_patch({"x": 3})
    assert model.flag is False
    _ = model.merge_patch({"x": 4, "plain": 1})
    assert model.flag is True


def test_an_edge_patch_of_a_profile_only_field_is_refused() -> None:
    for patch in ({"profile_note": "x"}, {"profileNote": "x"}, {"profile_note": None}):
        edge = Mod[Edge].model_validate(mod_json())
        with pytest.raises(
            PatchError, match="'profileNote' belongs to the profile"
        ) as raised:
            _ = edge.merge_patch(patch)
        assert str(raised.value.path) == "profile_note"
        assert raised.value.kind == "invalid_value"
        assert edge.profile_note is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    # The profile layer takes it as any plain field.
    profile = Mod[Profile].model_validate(mod_json())
    _ = profile.merge_patch({"profile_note": "x"})
    assert profile.profile_note == "x"
