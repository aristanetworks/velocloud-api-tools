"""Tests for the layer-parametrized models, the field kinds and their carriers, display defaults and ``field_kind``."""

from typing import Annotated, Any, get_origin

import pytest
from layered_models import Mod, Rule, Seg, Settings, mod_json
from pydantic import MISSING, Field, TypeAdapter
from pyvelo.core import (
    ByPresence,
    Composed,
    ConfigContext,
    Default,
    Edge,
    EdgeOnly,
    Effective,
    Inheritable,
    Kind,
    Layered,
    Owned,
    Profile,
    Resolvable,
    Unconverted,
    field_kind,
    kind_default,
    layer_for,
)
from pyvelo.core.identity import SegmentIdentity
from pyvelo.core.layers import carrier

# Fields that hold their plain default in every dump and say nothing about ownership.
_PLAIN = {"legacy", "edge_note", "profile_note"}


def _dump(model: Mod[Edge] | Mod[Profile]) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude=_PLAIN)


def test_a_layered_model_needs_a_layer() -> None:
    with pytest.raises(TypeError, match=r"Mod\[Edge\]"):
        Mod.model_validate({})
    with pytest.raises(TypeError, match=r"Mod\[Edge\]"):
        Mod()


def test_a_class_with_a_layer_given_reports_it() -> None:
    class Wrapper(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
        pass

    assert Wrapper.layer() is Edge
    with pytest.raises(TypeError, match="parametrize"):
        Layered.layer()


def test_edge_inheritable_holds_owned() -> None:
    mod = Mod[Edge].model_validate({"segments": [], "enabled": False})
    assert mod.enabled == Owned(False)
    assert mod.level == Owned(MISSING)
    assert mod.level.is_local is False
    assert mod.enabled.is_local is True
    assert mod.enabled.local is False


def test_edge_owned_with_a_stored_null_is_not_local() -> None:
    mod = Mod[Edge].model_validate(mod_json(level=None))
    assert mod.level.stored is None
    assert mod.level.is_local is False
    assert mod.level.local is MISSING


def test_edge_inheritable_validates_its_inner_type() -> None:
    mod = Mod[Edge].model_validate(mod_json(settings={"a": 4}))
    assert mod.settings.stored == Settings(a=4)
    with pytest.raises(ValueError, match="enabled"):
        Mod[Edge].model_validate(mod_json(enabled="maybe"))


def test_an_owned_given_as_input_is_copied() -> None:
    given = Owned(False)
    mod = Mod[Edge].model_validate(mod_json(enabled=given))
    assert mod.enabled == given
    assert mod.enabled is not given


def test_profile_inheritable_reads_the_display_default() -> None:
    first = Mod[Profile].model_validate({"segments": []})
    second = Mod[Profile].model_validate({"segments": []})
    assert first.enabled is True
    assert "enabled" not in first.model_fields_set
    assert first.settings == Settings()
    assert first.settings is not second.settings


def test_composed_inheritable_is_the_inner_type_with_the_display_default() -> None:
    mod = Mod[Composed].model_validate({"segments": [], "enabled": False})
    assert mod.enabled is False
    assert mod.level is None
    assert Mod[Composed].model_validate({"segments": []}).enabled is True


def test_wrong_layer_fields_hold_missing() -> None:
    edge = Mod[Edge].model_validate({"profileNote": "x", "segments": []})
    assert edge.profile_note is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    assert "profile_note" not in edge.model_fields_set
    profile = Mod[Profile].model_validate({"edgeNote": "x", "segments": []})
    assert profile.edge_note is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    assert "edge_note" not in profile.model_fields_set


def test_wrong_layer_fields_hold_missing_when_the_key_is_absent() -> None:
    assert Mod[Edge].model_validate({"segments": []}).profile_note is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    assert Mod[Profile].model_validate({"segments": []}).edge_note is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    assert Mod[Edge].model_validate({"segments": []}).edge_note == ""
    assert Mod[Profile].model_validate({"segments": []}).profile_note == ""


def test_the_edge_dump_omits_what_is_not_stored() -> None:
    for raw in (
        {"segments": []},
        {"segments": [], "level": None},
        {"segments": [], "enabled": False, "settings": {"a": 2, "b": False}},
    ):
        edge = Mod[Edge].model_validate(raw)
        assert _dump(edge) == raw
        python = edge.model_dump(by_alias=True, exclude=_PLAIN)
        assert all(not isinstance(v, Owned) for v in python.values())
        assert MISSING not in python.values()


def test_the_edge_dump_leaves_out_the_other_layers_fields() -> None:
    edge = Mod[Edge].model_validate(mod_json())
    assert "profileNote" not in edge.model_dump(by_alias=True)
    assert edge.model_dump(by_alias=True)["edgeNote"] == ""
    profile = Mod[Profile].model_validate(mod_json())
    assert "edgeNote" not in profile.model_dump(by_alias=True)
    assert profile.model_dump(by_alias=True)["profileNote"] == ""


def test_the_compact_dump_follows_the_same_rules() -> None:
    edge = Mod[Edge].model_validate(
        mod_json(enabled=False, settings={"a": 2, "b": False})
    )
    compact = edge.model_dump(context={"compact": True}, by_alias=True)
    assert compact["enabled"] is False
    assert compact["settings"] == {"a": 2}
    assert "level" not in compact
    assert "profileNote" not in compact
    unstored = Mod[Edge].model_validate(mod_json())
    assert "enabled" not in unstored.model_dump(context={"compact": True})


def test_the_profile_dump_writes_only_what_the_profile_stores() -> None:
    profile = Mod[Profile].model_validate({"segments": []})
    assert _dump(profile) == {"segments": []}
    profile.enabled = True  # equal to the display default, but assigned
    assert _dump(profile) == {"segments": [], "enabled": True}
    profile.settings.a = 5  # in place: differs from the display default
    assert _dump(profile) == {
        "segments": [],
        "enabled": True,
        "settings": {"a": 5, "b": False},
    }


def test_assignment_to_an_edge_inheritable_raises() -> None:
    edge = Mod[Edge].model_validate(mod_json())
    with pytest.raises(TypeError) as raised:
        edge.enabled = True
    message = str(raised.value)
    assert "set_local" in message
    assert '{"enabled": null}' in message
    assert edge.enabled == Owned(MISSING)

    profile = Mod[Profile].model_validate(mod_json())
    profile.enabled = False
    assert profile.enabled is False


def test_edge_assignment_of_other_fields_is_ordinary() -> None:
    edge = Mod[Edge].model_validate(mod_json())
    edge.edge_note = "kept"
    assert edge.edge_note == "kept"


def test_effective_and_composed_are_read_only() -> None:
    composed = Mod[Composed].model_validate({"segments": []})
    with pytest.raises(TypeError, match="state.edge"):
        composed.enabled = False
    with pytest.raises(TypeError, match="state.edge"):
        composed.edge_note = "x"
    with pytest.raises(TypeError, match="Effective"):
        Mod[Effective].model_validate({})


def test_a_model_copy_update_cannot_replace_who_owns_an_edge_value() -> None:
    edge = Mod[Edge].model_validate(mod_json())
    with pytest.raises(TypeError, match=r"set_local.*\{\"enabled\": null\}") as raised:
        edge.model_copy(update={"enabled": True})
    assert "Mod[Edge].enabled" in str(raised.value)
    assert edge.enabled == Owned(MISSING)
    # What is not an Inheritable is copied with the update, as for any model.
    assert edge.model_copy(update={"edge_note": "kept"}).edge_note == "kept"
    assert edge.model_copy().enabled == Owned(MISSING)
    profile = Mod[Profile].model_validate(mod_json())
    assert profile.model_copy(update={"enabled": False}).enabled is False


def test_a_model_copy_update_of_a_read_only_layer_raises() -> None:
    composed = Mod[Composed].model_validate({"segments": []})
    for key in ("enabled", "edge_note"):
        with pytest.raises(TypeError, match="state.edge"):
            composed.model_copy(update={key: False})
    assert composed.model_copy().enabled == composed.enabled


def test_every_kind_on_the_effective_layer_refuses_validation() -> None:
    for kind in (
        Kind.INHERITABLE,
        Kind.EDGE_ONLY,
        Kind.PROFILE_ONLY,
        Kind.ADDITIVE,
        Kind.UNCONVERTED,
    ):
        generic = carrier(kind, int)  # over the layer variable, as a field declares it
        origin: Any = get_origin(generic)
        effective: Any = origin[Effective, int]
        with pytest.raises(TypeError, match="effective view"):
            TypeAdapter(effective).validate_python(1)


def test_layers_report_config_context() -> None:
    assert (
        Mod[Edge].model_validate({"segments": []}).config_context is ConfigContext.EDGE
    )
    assert (
        Mod[Profile].model_validate({"segments": []}).config_context
        is ConfigContext.PROFILE
    )
    assert Mod[Composed].model_validate({"segments": []}).config_context is None
    segment = {"segment": {"name": "Global Segment"}}
    edge = Mod[Edge].model_validate({"segments": [segment]})
    assert edge.segments[0].config_context is ConfigContext.EDGE
    profile = Mod[Profile].model_validate({"segments": [segment]})
    assert profile.segments[0].config_context is ConfigContext.PROFILE
    assert type(edge.segments[0]) is Seg[Edge]


def test_layer_for_maps_a_config_level_to_its_layer() -> None:
    assert layer_for(ConfigContext.EDGE) is Edge
    assert layer_for(ConfigContext.PROFILE) is Profile


def test_field_kind_unwraps_each_kind() -> None:
    fields = Mod[Edge].model_fields
    expected: dict[str, tuple[Kind, object]] = {
        "segments": (Kind.STRUCTURAL, list[Seg[Edge]]),
        "enabled": (Kind.INHERITABLE, bool),
        "level": (Kind.INHERITABLE, int | None),
        "settings": (Kind.INHERITABLE, Settings),
        "edge_note": (Kind.EDGE_ONLY, str),
        "profile_note": (Kind.PROFILE_ONLY, str),
        "legacy": (Kind.UNCONVERTED, list[int]),
    }
    for name, (kind, inner) in expected.items():
        found = field_kind(fields[name])
        assert found.kind is kind, name
        assert found.inner_type == inner, name
    assert isinstance(field_kind(fields["enabled"]).encoding, ByPresence)
    assert field_kind(fields["segments"]).encoding is None
    assert field_kind(fields["edge_note"]).encoding is None
    defaults = [
        m for m in field_kind(fields["enabled"]).metadata if isinstance(m, Default)
    ]
    assert [d.make() for d in defaults] == [True]

    seg_fields = Seg[Edge].model_fields
    rules = field_kind(seg_fields["rules"])
    assert rules.kind is Kind.ADDITIVE
    assert rules.inner_type == list[Rule]
    assert field_kind(seg_fields["legacy"]).inner_type == int | None


def test_field_kind_is_the_same_for_an_unparametrized_class_and_every_layer() -> None:
    for model in (Mod, Mod[Edge], Mod[Profile], Mod[Composed], Mod[Effective]):
        assert field_kind(model.model_fields["enabled"]).kind is Kind.INHERITABLE
        assert field_kind(model.model_fields["enabled"]).inner_type is bool


def test_field_kind_is_cached_per_field_info() -> None:
    info = Mod[Edge].model_fields["enabled"]
    assert field_kind(info) is field_kind(info)


def test_field_kind_finds_a_marker_inside_the_kind() -> None:
    class Holder(Layered[Edge]):
        port: Annotated[
            Inheritable[Annotated[int, Resolvable(SegmentIdentity, "segment_id")]],
            ByPresence(),
            Default(0),
        ] = Field(default=...)

    found = field_kind(Holder.model_fields["port"])
    assert found.inner_type is int
    assert any(isinstance(m, Resolvable) for m in found.metadata)
    assert isinstance(found.metadata[0], ByPresence)  # the outer metadata comes first


def test_a_default_is_fresh_each_time() -> None:
    by_value = Default([1])
    assert by_value.make() == [1]
    assert by_value.make() is not by_value.make()
    by_factory = Default(factory=Settings)
    assert by_factory.make() == Settings()
    assert by_factory.make() is not by_factory.make()
    assert Default(None).make() is None
    with pytest.raises(TypeError):
        _ = Default()
    with pytest.raises(TypeError):
        _ = Default(1, factory=int)


def test_every_inheritable_needs_one_default_and_one_encoding() -> None:
    with pytest.raises(TypeError, match=r"Bad\.flag.*Default"):

        class Bad(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            flag: Annotated[Inheritable[bool], ByPresence()] = Field(default=...)

    with pytest.raises(TypeError, match=r"Bad2\.flag.*encoding"):

        class Bad2(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            flag: Annotated[Inheritable[bool], Default(True)] = Field(default=...)

    with pytest.raises(TypeError, match=r"Bad3\.flag.*Default"):

        class Bad3(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            flag: Annotated[
                Inheritable[bool], ByPresence(), Default(True), Default(False)
            ] = Field(default=...)


def test_only_an_inheritable_may_carry_a_default() -> None:
    with pytest.raises(TypeError, match=r"Bad\.note.*Default"):

        class Bad(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            note: Annotated[EdgeOnly[str], Default("")] = kind_default("")


def test_construction_by_keyword() -> None:
    assert Mod[Profile](enabled=False).enabled is False
    assert Mod[Edge](enabled=Owned(False)).enabled == Owned(False)
    empty = Mod[Edge]()
    assert empty.enabled == Owned(MISSING)
    assert empty.level == Owned(MISSING)
    assert empty.settings == Owned(MISSING)


def test_json_schema_generation_succeeds() -> None:
    for model in (Mod[Edge], Mod[Profile], Mod[Composed]):
        assert "enabled" in model.model_json_schema()["properties"]


def test_kind_default_gives_a_value_or_a_factory() -> None:
    class Holder(Layered[Edge]):
        note: EdgeOnly[str] = kind_default("x")
        items: Unconverted[list[int]] = kind_default(factory=list)
        nothing: Unconverted[int | None] = kind_default(None)

    first = Holder.model_validate({})
    second = Holder.model_validate({})
    assert (first.note, first.items, first.nothing) == ("x", [], None)
    assert first.items is not second.items
    assert Holder.model_fields["note"].is_required() is False
    with pytest.raises(TypeError, match="kind_default"):
        _ = kind_default()
    with pytest.raises(TypeError, match="kind_default"):
        _ = kind_default(1, factory=int)
