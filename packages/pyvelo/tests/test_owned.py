"""Tests for ``Owned``: the encodings, the ownership intents, coupled groups and the binding of an edge layer to its profile."""

import copy
from typing import Annotated, Any, ClassVar

import pytest
from layered_models import Flagged, Mod, PairLocal, Reset, Settings, mod_json
from pydantic import MISSING, Field, ValidationError
from pyvelo.client.exceptions import (
    DetachedObjectError,
    GroupOwnershipError,
    UnboundError,
)
from pyvelo.core import (
    ByPresence,
    ConfigContext,
    Default,
    DefaultingScalar,
    Edge,
    Inheritable,
    Layered,
    MemberOf,
    Owned,
    OwnedGroup,
    PatchError,
    Profile,
    ServerOwned,
    VcoModel,
    bind_layers,
)
from pyvelo.core.layers import L, inner_adapter

# Fields that hold their plain default in every dump and say nothing about ownership.
_PLAIN = {"legacy", "edge_note", "profile_note"}


def _dump(model: Mod[Edge] | Mod[Profile]) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude=_PLAIN)


def _segment(segment_id: int, name: str, **keys: object) -> dict[str, object]:
    return {"segment": {"segmentId": segment_id, "name": name}, **keys}


def _edge(**keys: object) -> Mod[Edge]:
    return Mod[Edge].model_validate(mod_json(**keys))


def _bound(
    edge: dict[str, object] | None = None, profile: dict[str, object] | None = None
) -> tuple[Mod[Edge], Mod[Profile]]:
    e = Mod[Edge].model_validate(mod_json(**(edge or {})))
    p = Mod[Profile].model_validate(mod_json(**(profile or {})))
    bind_layers(e, p)
    return e, p


# ---------------------------------------------------------------------------
# ByPresence
# ---------------------------------------------------------------------------


def test_by_presence_counts_false_zero_and_empty_as_local() -> None:
    e = _edge(enabled=False, level=0, settings={})
    assert e.enabled.is_local and e.enabled.local is False
    assert e.level.is_local and e.level.local == 0
    assert e.settings.is_local and e.settings.local == Settings()
    for name in ("enabled", "level", "settings"):
        assert getattr(_edge(**{name: None}), name).is_local is False
        assert getattr(_edge(), name).is_local is False


def test_by_presence_set_local_inherit_localize() -> None:
    e = _edge()
    e.enabled.set_local(False)
    assert _dump(e)["enabled"] is False
    e.enabled.inherit()
    assert "enabled" not in _dump(e)
    assert e.enabled.stored is MISSING

    e, _ = _bound(profile={"enabled": False})
    assert e.enabled.localize() is False
    assert _dump(e)["enabled"] is False

    e, _ = _bound()
    assert e.enabled.localize() is True  # the display default
    assert _dump(e)["enabled"] is True


def test_localize_returns_the_stored_object_for_editing() -> None:
    e, p = _bound(profile={"settings": {"a": 5}})
    before = _dump(p)
    edited = e.settings.localize()
    assert edited is e.settings.stored
    edited.a = 9
    assert _dump(e)["settings"]["a"] == 9
    assert p.settings.a == 5
    assert _dump(p) == before


def test_localize_keeps_a_local_value() -> None:
    e, _ = _bound({"settings": {"a": 3}}, {"settings": {"a": 5}})
    kept = e.settings.stored
    assert e.settings.localize() is kept
    assert _dump(e)["settings"]["a"] == 3


def test_localizing_a_null_leaves_it_inherited() -> None:
    # A null at a ByPresence key means "inherit", so a local null cannot be stored.
    e, _ = _bound()
    assert e.level.localize() is None
    assert e.level.is_local is False
    assert e.level.stored is MISSING


def test_set_local_validates() -> None:
    e = _edge(level=3)
    with pytest.raises(PatchError) as exc:
        e.level.set_local("x")  # pyright: ignore[reportArgumentType]
    assert "invalid value" in str(exc.value)
    assert exc.value.path.parts == ("level",)
    assert e.level.stored == 3
    with pytest.raises(PatchError, match="boolean"):
        e.level.set_local(True)
    assert e.level.stored == 3


def test_the_inner_adapter_validates_the_type_inside_the_kind() -> None:
    adapter = inner_adapter(Mod[Edge], "settings")
    assert inner_adapter(Mod[Edge], "settings") is adapter
    assert adapter.validate_python({"a": 3}) == Settings(a=3)
    with pytest.raises(ValidationError):
        adapter.validate_python({"a": "x"})


def test_set_local_applies_the_value_validators_of_the_field() -> None:
    class Lenient(Layered[L]):
        flag: Annotated[
            Inheritable[Annotated[bool, DefaultingScalar(bool, default=False)]],
            ByPresence(),
            Default(True),
        ] = Field(default=...)

    owner = Lenient[Edge].model_validate({"flag": "garbage"})
    assert owner.flag.stored is False  # the data is tolerated, as on a read
    with pytest.raises(PatchError, match="invalid value"):  # a set is not
        owner.flag.set_local("garbage")  # pyright: ignore[reportArgumentType]
    owner.flag.set_local(True)
    assert owner.flag.stored is True


@pytest.mark.parametrize(
    ("field", "bad", "cause"),
    [("pair_on", "x", True), ("level", "x", True), ("level", True, False)],
)
def test_set_local_and_merge_patch_refuse_a_value_alike(
    field: str, bad: object, cause: bool
) -> None:
    edge = _local_pair()
    profile = Mod[Profile].model_validate(mod_json())
    with pytest.raises(PatchError) as from_set_local:
        getattr(edge, field).set_local(bad)
    with pytest.raises(PatchError) as from_patch:
        profile.merge_patch({field: bad})
    mine, theirs = from_set_local.value, from_patch.value
    assert mine.kind == theirs.kind == "invalid_value"
    assert mine.path == theirs.path
    assert mine.path.parts == (field,)  # the python name, not the wire key
    assert str(mine) == str(theirs)
    if cause:
        assert isinstance(mine.__cause__, ValidationError)
        assert isinstance(theirs.__cause__, ValidationError)
        assert mine.__cause__.errors(include_input=False) == (
            theirs.__cause__.errors(include_input=False)
        )
        assert "'x'" not in str(mine.__cause__)  # the cause quotes no input
    else:
        assert mine.__cause__ is theirs.__cause__ is None


def test_set_local_refuses_a_null() -> None:
    e = _edge(level=3)
    with pytest.raises(PatchError, match=r'null.*\{"level": null\}') as exc:
        e.level.set_local(None)
    assert e.level.stored == 3
    assert (exc.value.kind, exc.value.path) == ("null", "level")


def test_set_local_does_not_alias_the_callers_object() -> None:
    e = _edge()
    given = Settings(a=2)
    e.settings.set_local(given)
    assert e.settings.stored == given
    assert e.settings.stored is not given


def test_an_owned_given_as_input_does_not_alias_the_callers_object() -> None:
    given = Owned(Settings(a=2))
    e = _edge(settings=given)
    assert e.settings is not given
    assert e.settings.stored == given.stored
    assert e.settings.stored is not given.stored
    e.settings.localize().a = 7
    assert given.stored.a == 2  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportAttributeAccessIssue]


def test_an_owned_without_an_owner_reads_by_presence() -> None:
    owned = Owned(Flagged(override=False))
    assert owned.is_local
    owned.set_local(Flagged(value=1))
    assert owned.stored == Flagged(value=1)
    owned.inherit()
    assert owned.stored is MISSING
    with pytest.raises(UnboundError):
        owned.localize()


def test_a_deep_copy_of_one_owned_has_no_owner() -> None:
    copied = copy.deepcopy(_edge(settings={"a": 4}).settings)
    assert copied.stored == Settings(a=4)
    copied.inherit()
    with pytest.raises(UnboundError):
        copied.localize()


# ---------------------------------------------------------------------------
# ByFlag
# ---------------------------------------------------------------------------


def test_by_flag() -> None:
    e = _edge(
        segments=[
            _segment(1, "Corp", mapping={"override": False, "value": 3}),
            _segment(2, "Guest", mapping={"override": True, "value": 5}),
        ]
    )
    corp, guest = e.segments
    assert corp.mapping.stored == Flagged(override=False, value=3)
    assert corp.mapping.is_local is False
    assert corp.mapping.local is MISSING
    assert guest.mapping.is_local is True

    given = Flagged(value=4)
    corp.mapping.set_local(given)
    assert _dump(e)["segments"][0]["mapping"] == {"override": True, "value": 4}
    assert given.override is False  # the caller's object is not the stored one
    assert corp.mapping.is_local

    corp.mapping.inherit()
    assert "mapping" not in _dump(e)["segments"][0]


def test_by_flag_inherit_as_persists_a_reset_object() -> None:
    e = _edge(affinity={"override": True, "affinity": ["x"]})
    assert e.affinity.is_local
    e.affinity.inherit()
    assert e.affinity.is_local is False
    assert e.affinity.stored == Reset(override=False, affinity=[])
    assert _dump(e)["affinity"] == {"override": False, "affinity": []}


def test_by_flag_localize_forces_the_flag() -> None:
    e, _ = _bound(
        {"segments": [_segment(1, "Corp")]}, {"affinity": {"affinity": ["a"]}}
    )
    got = e.affinity.localize()
    assert got == Reset(override=True, affinity=["a"])
    assert _dump(e)["affinity"] == {"override": True, "affinity": ["a"]}


# ---------------------------------------------------------------------------
# Coupled groups
# ---------------------------------------------------------------------------


def _local_pair() -> Mod[Edge]:
    return _edge(pairA={"a": 2}, pairB={"a": 3}, pairOn=True)


def test_group_inherit_removes_every_member() -> None:
    e = _local_pair()
    assert e.pair.is_local
    e.pair.inherit()
    assert e.pair.is_local is False
    assert {"pairA", "pairB", "pairOn"}.isdisjoint(_dump(e))


def test_group_set_local_needs_a_full_bundle() -> None:
    with pytest.raises(ValidationError):
        PairLocal(pair_a=Settings(), pair_b=Settings())  # pyright: ignore[reportCallIssue]
    e = _edge()
    e.pair.set_local(PairLocal(pair_a=Settings(a=2), pair_b=Settings(), pair_on=True))
    assert e.pair.is_local
    dump = _dump(e)
    assert (dump["pairA"]["a"], dump["pairB"]["a"], dump["pairOn"]) == (2, 1, True)
    with pytest.raises(TypeError, match="PairLocal"):
        e.pair.set_local({"pair_a": {}})  # pyright: ignore[reportArgumentType]


def test_a_member_cannot_be_inherited_alone() -> None:
    e = _local_pair()
    for name in ("pair_b", "pair_a"):
        with pytest.raises(GroupOwnershipError) as exc:
            getattr(e, name).inherit()
        assert (exc.value.group, exc.value.field) == ("pair", name)
        assert 'group "pair"' in str(exc.value)
        assert '{"pairA": null}' in str(exc.value)
        assert "pair.inherit()" in str(exc.value)
    assert e.pair.is_local


def test_a_member_is_set_only_when_the_group_is_local() -> None:
    e = _edge()
    for intent in (
        lambda: e.pair_b.set_local(Settings()),
        lambda: e.pair_a.set_local(Settings()),
    ):
        with pytest.raises(GroupOwnershipError, match="pair") as exc:
            intent()
        assert exc.value.group == "pair"
    assert {"pairA", "pairB"}.isdisjoint(_dump(e))
    e, _ = _bound()
    with pytest.raises(GroupOwnershipError):  # it would leave the others behind
        e.pair_b.localize()
    assert e.pair_b.stored is MISSING

    e = _local_pair()
    e.pair_b.set_local(Settings(a=7))
    assert _dump(e)["pairB"]["a"] == 7


def test_group_localize_copies_every_member_from_the_profile() -> None:
    e, p = _bound(profile={"pairA": {"a": 2}, "pairB": {"a": 3}, "pairOn": True})
    before = _dump(p)
    assert e.pair.localize() is None
    assert e.pair.is_local
    dump = _dump(e)
    assert (dump["pairA"]["a"], dump["pairB"]["a"], dump["pairOn"]) == (2, 3, True)
    e.pair_b.localize().a = 9
    assert p.pair_b.a == 3
    assert _dump(p) == before

    e, _ = _bound()
    e.pair.localize()
    assert _dump(e)["pairOn"] is False  # the display default
    assert _dump(e)["pairA"] == {"a": 1, "b": False}


def test_group_localize_needs_the_profile() -> None:
    e = _edge()
    with pytest.raises(UnboundError, match="loaded state"):
        e.pair.localize()
    assert e.pair.is_local is False
    assert {"pairA", "pairB", "pairOn"}.isdisjoint(_dump(e))


def test_the_group_handle_is_edge_only() -> None:
    p = Mod[Profile].model_validate(mod_json())
    with pytest.raises(AttributeError, match=r"Profile"):
        _ = p.pair  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(Mod.pair, OwnedGroup)


def test_a_group_checks_its_declaration() -> None:
    class Bundle(VcoModel):
        x: bool
        y: bool

    def declare(anchor: str = "x", bundle: type[VcoModel] = Bundle) -> type:
        class Group(Layered[L]):
            x: Annotated[
                Inheritable[bool], ByPresence(), MemberOf("g"), Default(False)
            ] = Field(default=...)
            y: Annotated[
                Inheritable[bool], ByPresence(), MemberOf("g"), Default(False)
            ] = Field(default=...)
            g: ClassVar[OwnedGroup] = OwnedGroup("g", anchor=anchor, bundle=bundle)

        return Group

    assert declare()

    with pytest.raises(TypeError, match="anchor.*nope"):
        declare(anchor="nope")

    class Short(VcoModel):
        x: bool

    with pytest.raises(TypeError, match=r"bundle.*Short.*\by\b"):
        declare(bundle=Short)

    with pytest.raises(TypeError, match=r"MemberOf.*h"):

        class Stray(Layered[L]):  # pyright: ignore[reportUnusedClass]
            x: Annotated[
                Inheritable[bool], ByPresence(), MemberOf("h"), Default(False)
            ] = Field(default=...)

    with pytest.raises(TypeError, match=r"Plain\.x.*Inheritable"):

        class Plain(Layered[L]):  # pyright: ignore[reportUnusedClass]
            x: Annotated[bool, MemberOf("g")] = True
            y: Annotated[
                Inheritable[bool], ByPresence(), MemberOf("g"), Default(False)
            ] = Field(default=...)
            g: ClassVar[OwnedGroup] = OwnedGroup("g", anchor="x", bundle=Bundle)


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_localize_on_an_unbound_object_raises() -> None:
    e = _edge()
    with pytest.raises(UnboundError, match=r"loaded state.*state\.edge"):
        e.level.localize()
    assert e.level.stored is MISSING


def test_binding_matches_segments_by_segment_id() -> None:
    e, _ = _bound(
        {"segments": [_segment(1, "Renamed"), _segment(2, "Guest")]},
        {
            "segments": [
                _segment(1, "Corp", mapping={"override": False, "value": 3}),
                _segment(5, "Other", mapping={"value": 9}),
            ]
        },
    )
    renamed, edge_only = e.segments
    assert renamed.mapping.localize() == Flagged(override=True, value=3)
    assert edge_only.mapping.localize() == Flagged(override=True, value=0)
    mappings = [s["mapping"] for s in _dump(e)["segments"]]
    assert mappings == [
        {"override": True, "value": 3},
        {"override": True, "value": 0},
    ]


def test_binding_to_no_profile_localizes_the_display_default() -> None:
    e = _edge(segments=[_segment(1, "Corp")])
    bind_layers(e, None)
    assert e.enabled.localize() is True
    assert e.segments[0].mapping.localize() == Flagged(override=True)


def test_bind_layers_takes_an_edge_layer_and_a_profile_layer() -> None:
    p = Mod[Profile].model_validate(mod_json())
    with pytest.raises(TypeError, match="edge layer"):
        bind_layers(p, None)  # pyright: ignore[reportArgumentType]


def test_a_deep_copy_keeps_the_binding() -> None:
    e, p = _bound(profile={"level": 5, "pairA": {"a": 8}})
    duplicate = copy.deepcopy(e)
    assert duplicate.level is not e.level
    assert duplicate.level.localize() == 5
    assert duplicate.level.is_local
    assert e.level.is_local is False
    assert e.level.stored is MISSING
    # the copy's owned values belong to the copy
    duplicate.enabled.set_local(False)
    assert e.enabled.stored is MISSING
    duplicate.enabled.inherit()
    assert duplicate.pair.localize() is None
    assert e.pair.is_local is False
    assert p.level == 5


def test_a_deep_copy_of_a_segment_keeps_its_binding() -> None:
    e, _ = _bound(
        {"segments": [_segment(1, "Corp")]},
        {"segments": [_segment(1, "Corp", mapping={"value": 3})]},
    )
    duplicate = copy.deepcopy(e)
    assert duplicate.segments[0].mapping.localize() == Flagged(override=True, value=3)
    assert e.segments[0].mapping.stored is MISSING


def test_a_shallow_copy_shares_the_owned_values() -> None:
    e, _ = _bound(profile={"level": 5})
    assert e.model_copy().level is e.level


def test_model_construct_attaches_and_fills_the_owned_values() -> None:
    built = Mod[Edge].model_construct(level=Owned(3))
    assert built.level.is_local
    assert built.enabled.stored is MISSING
    built.enabled.set_local(True)
    assert built.enabled.is_local


# ---------------------------------------------------------------------------
# Detached owners
# ---------------------------------------------------------------------------


def test_intents_on_a_detached_owner_raise() -> None:
    e, _ = _bound(
        {"level": 1, "segments": [_segment(1, "Corp")]},
        {"settings": {"a": 4}, "level": 2},
    )
    e.mark_detached()
    for call in (
        lambda: e.level.set_local(2),
        lambda: e.level.inherit(),
        lambda: e.level.localize(),
        lambda: e.settings.localize(),
        lambda: e.segments[0].mapping.set_local(Flagged()),
        lambda: e.pair.inherit(),
        lambda: e.pair.localize(),
        lambda: e.pair.set_local(
            PairLocal(pair_a=Settings(), pair_b=Settings(), pair_on=True)
        ),
    ):
        with pytest.raises(DetachedObjectError, match="take it again"):
            call()
    assert e.level.stored == 1
    assert e.level.is_local
    assert e.settings.stored is MISSING


def test_a_deep_copy_of_a_detached_object_takes_intents() -> None:
    e, _ = _bound({"level": 1}, {"level": 2})
    e.mark_detached()
    duplicate = copy.deepcopy(e)
    duplicate.level.set_local(3)
    assert duplicate.level.stored == 3
    assert e.level.stored == 1


def test_the_config_level_reaches_inside_owned() -> None:
    e = _edge(settings={"a": 2}).for_edge()
    assert e.settings.stored.config_context is ConfigContext.EDGE  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportAttributeAccessIssue]


def test_detach_marking_reaches_inside_owned() -> None:
    e = _edge(settings={"a": 2}, segments=[_segment(1, "Corp", mapping={"value": 1})])
    marked = e.mark_detached()
    inside = e.settings.stored
    assert isinstance(inside, Settings)
    assert ((("settings",), inside)) in [(place, model) for place, model in marked]
    assert inside.is_detached
    assert e.segments[0].mapping.stored.is_detached  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportAttributeAccessIssue]
    with pytest.raises(DetachedObjectError):
        inside.a = 3
    assert inside.a == 2


# ---------------------------------------------------------------------------
# localize clears the profile's server-assigned IDs
# ---------------------------------------------------------------------------


class _Item(VcoModel):
    name: str
    rule_id: Annotated[str | MISSING, ServerOwned()] = MISSING
    counter: Annotated[int, ServerOwned()] = 7


class _Holder(VcoModel):
    rule_id: Annotated[str | None | MISSING, ServerOwned()] = MISSING
    items: list[_Item] = Field(default_factory=list)
    by_name: dict[str, _Item] = Field(default_factory=dict)
    inner: _Item | None = None


class _IdMod(Layered[L]):
    segments: list[Any] = Field(default_factory=list)
    holder: Annotated[Inheritable[_Holder], ByPresence(), Default(factory=_Holder)] = (
        Field(default=...)
    )


def test_localize_clears_server_owned_fields_at_every_depth():
    profile = _IdMod[Profile].model_validate(
        {
            "segments": [],
            "holder": {
                "rule_id": "p",
                "items": [{"name": "a", "rule_id": "1", "counter": 9}],
                "by_name": {"k": {"name": "b", "rule_id": "2"}},
                "inner": {"name": "c", "rule_id": "3", "counter": 4},
            },
        }
    )
    edge = _IdMod[Edge].model_validate({"segments": []})
    bind_layers(edge, profile)
    holder = edge.holder.localize()
    assert holder.rule_id is MISSING
    (item,) = holder.items
    # MISSING where the field accepts it, else the field's default.
    assert (item.name, item.rule_id, item.counter) == ("a", MISSING, 7)
    assert holder.by_name["k"].rule_id is MISSING
    assert holder.inner is not None
    assert (holder.inner.rule_id, holder.inner.counter) == (MISSING, 7)
    assert "rule_id" not in holder.items[0].model_fields_set
    # The profile is a copy away, and keeps them.
    assert profile.holder.rule_id == "p"
    assert profile.holder.items[0].rule_id == "1"
    assert profile.holder.items[0].counter == 9


def test_clear_server_owned_changes_the_model_in_place():
    from pyvelo.core.owned import clear_server_owned

    item = _Item(name="a", rule_id="1")
    assert clear_server_owned(item) is item
    assert item.rule_id is MISSING


def test_an_owned_is_not_a_condition():
    owned = Owned(True)
    with pytest.raises(TypeError, match=r"\.local.*\.stored.*\.is_local") as caught:
        bool(owned)
    assert "state.effective" in str(caught.value)
    assert ".value" in str(caught.value)
    with pytest.raises(TypeError):
        if owned:
            pass


def test_an_owned_equals_only_an_owned():
    assert (Owned(True) == True) is False
    assert Owned(True) == Owned(True)


def test_each_intent_on_a_member_has_its_own_refusal() -> None:
    e, _ = _bound()
    with pytest.raises(GroupOwnershipError) as exc:
        e.pair_b.set_local(Settings())
    said = str(exc.value)
    assert "setting one member" in said
    assert '{"pairB": <value>}' in said
    assert "every member" not in said
    with pytest.raises(GroupOwnershipError) as exc:
        e.pair_b.localize()
    said = str(exc.value)
    # Localizing one member localizes the group first, then the member: the
    # patch for it is the member alone.
    assert "setting one member" not in said
    assert "pair.localize()" in said
    assert '{"pairB": <value>}' in said
    assert "every member" not in said


def test_the_group_bundle_refusal_gives_the_patch() -> None:
    e = _edge()
    with pytest.raises(TypeError) as exc:
        e.pair.set_local({"pair_a": {}})  # pyright: ignore[reportArgumentType]
    said = str(exc.value)
    assert '{"pairA": ..., "pairB": ..., "pairOn": ...}' in said
