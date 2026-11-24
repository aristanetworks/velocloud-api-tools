"""The effective view: each value of an edge layer and a profile layer with its origin.

One test per row of the resolution table, then the edge with no row, the
read-only view and the dump the comparison with the server reads.
"""

from typing import Annotated, Any

import pytest
from layered_models import Flagged, Mod, Rule, Settings, mod_json
from pydantic import MISSING, Field
from pyvelo.core import (
    Authority,
    ByFlag,
    Default,
    Edge,
    EdgeOnly,
    Effective,
    Inheritable,
    Layered,
    Origin,
    Owned,
    Profile,
    Resolved,
    effective_dump,
    effective_view,
    kind_default,
)
from pyvelo.core.layers import L


def _seg(segment_id: int, name: str = "S", **keys: object) -> dict[str, object]:
    return {"segment": {"segmentId": segment_id, "name": name}, **keys}


def _view(edge: dict[str, object] | None, profile: dict[str, object]) -> Mod[Effective]:
    p = Mod[Profile].model_validate(mod_json(**profile))
    e = None if edge is None else Mod[Edge].model_validate(mod_json(**edge))
    return effective_view(e, p, Mod[Effective])


def _resolved(view: Mod[Effective]) -> dict[str, Resolved[object]]:
    return effective_dump(view)[1]


def test_a_local_edge_value_wins() -> None:
    view = _view({"level": 5}, {"level": 2})
    assert view.level == Resolved(Origin.EDGE, 5, "level")


def test_an_inherited_value_comes_from_the_profile() -> None:
    view = _view({}, {"level": 2, "settings": {"a": 9}})
    assert view.level == Resolved(Origin.PROFILE, 2, "level")
    assert view.settings == Resolved(Origin.PROFILE, Settings(a=9), "settings")


def test_a_stored_null_at_the_edge_is_inherited() -> None:
    view = _view({"level": None}, {"level": 2})
    assert view.level == Resolved(Origin.PROFILE, 2, "level")


def test_a_value_neither_layer_stores_is_a_display_default() -> None:
    view = _view({}, {})
    assert view.enabled == Resolved(Origin.FACTORY, True, "enabled")
    assert view.level == Resolved(Origin.FACTORY, None, "level")
    assert view.settings == Resolved(Origin.FACTORY, Settings(), "settings")


def test_a_profile_value_stored_as_the_default_is_the_profiles() -> None:
    view = _view({}, {"enabled": True})
    assert view.enabled == Resolved(Origin.PROFILE, True, "enabled")


def test_a_profile_value_changed_in_place_counts_as_stored() -> None:
    profile = Mod[Profile].model_validate(mod_json())
    profile.settings.a = 7
    view = effective_view(None, profile, Mod[Effective])
    assert view.settings == Resolved(Origin.PROFILE, Settings(a=7), "settings")


def test_a_factory_value_is_a_copy_of_the_default() -> None:
    first = _view({}, {}).settings.value
    second = _view({}, {}).settings.value
    first.a = 99
    assert second.a == 1


class _Plain(Layered[L]):
    """Like ``Mod.mapping`` but without ``presence_wins``."""

    mapping: Annotated[
        Inheritable[Flagged], ByFlag("override"), Default(factory=Flagged)
    ] = Field(default=...)


def test_presence_wins_uses_a_present_edge_object_with_a_false_flag() -> None:
    profile = mod_json(
        segments=[_seg(1, mapping={"override": True, "value": 7})],
    )
    edge = mod_json(
        segments=[_seg(1, mapping={"override": False, "value": 3})],
    )
    view = _view(edge, profile)
    assert view.segments[0].mapping == Resolved(
        Origin.EDGE, Flagged(override=False, value=3), "segments[0].mapping"
    )
    # An object the edge stores true is local by the flag, as usual.
    local = _view(
        mod_json(segments=[_seg(1, mapping={"override": True, "value": 4})]), profile
    )
    assert local.segments[0].mapping.origin is Origin.EDGE


def test_without_presence_wins_a_false_flag_inherits() -> None:
    profile = _Plain[Profile].model_validate(
        {"mapping": {"override": True, "value": 7}}
    )
    edge = _Plain[Edge].model_validate({"mapping": {"override": False, "value": 3}})
    view = effective_view(edge, profile, _Plain[Effective])
    assert view.mapping == Resolved(
        Origin.PROFILE, Flagged(override=True, value=7), "mapping"
    )


def test_the_authority_rule_overrides_the_edge() -> None:
    view = _view({"enabled": True}, {"enabled": False})
    assert view.enabled == Resolved(Origin.PROFILE, False, "enabled")


def test_the_authority_rule_leaves_other_values_alone() -> None:
    assert _view({"enabled": False}, {"enabled": True}).enabled == Resolved(
        Origin.EDGE, False, "enabled"
    )
    assert _view({}, {}).enabled.origin is Origin.FACTORY


def test_edge_only_and_profile_only_take_their_layer() -> None:
    view = _view({"edgeNote": "e"}, {"profileNote": "p"})
    assert view.edge_note == Resolved(Origin.EDGE, "e", "edgeNote")
    assert view.profile_note == Resolved(Origin.PROFILE, "p", "profileNote")


def test_additive_entries_are_edge_first() -> None:
    edge = mod_json(segments=[_seg(1, rules=[{"name": "e1"}])])
    profile = mod_json(segments=[_seg(1, rules=[{"name": "p1"}, {"name": "p2"}])])
    rules = _view(edge, profile).segments[0].rules
    assert rules == [
        Resolved(Origin.EDGE, Rule(name="e1"), "segments[0].rules[0]"),
        Resolved(Origin.PROFILE, Rule(name="p1"), "segments[0].rules[0]"),
        Resolved(Origin.PROFILE, Rule(name="p2"), "segments[0].rules[1]"),
    ]


def test_segments_profile_order_then_edge_only() -> None:
    profile = mod_json(segments=[_seg(1, "A"), _seg(2, "B", rules=[{"name": "p"}])])
    edge = mod_json(
        segments=[
            _seg(3, "C", mapping={"override": True, "value": 1}),
            _seg(2, "B", mapping={"override": True, "value": 2}, rules=[{"name": "e"}]),
        ]
    )
    segments = _view(edge, profile).segments
    assert [s.segment.segment_id for s in segments] == [1, 2, 3]
    a, b, c = segments
    # A the edge lacks: the profile's, then the display default.
    assert a.mapping == Resolved(Origin.FACTORY, Flagged(), "segments[0].mapping")
    assert a.rules == []
    # B in both: the edge's value, at the edge's position; additive entries
    # each from their own layer's position.
    assert b.mapping == Resolved(
        Origin.EDGE, Flagged(override=True, value=2), "segments[1].mapping"
    )
    assert b.rules == [
        Resolved(Origin.EDGE, Rule(name="e"), "segments[1].rules[0]"),
        Resolved(Origin.PROFILE, Rule(name="p"), "segments[1].rules[0]"),
    ]
    # C only the edge has.
    assert c.segment.name == "C"
    assert c.mapping == Resolved(
        Origin.EDGE, Flagged(override=True, value=1), "segments[0].mapping"
    )


def test_unconverted_is_missing() -> None:
    view = _view({"legacy": [1]}, {"legacy": [2]})
    assert vars(view)["legacy"] is MISSING
    seg = _view(mod_json(segments=[_seg(1)]), mod_json(segments=[_seg(1)])).segments[0]
    assert vars(seg)["legacy"] is MISSING


def test_without_an_edge_row_every_origin_is_profile_or_factory() -> None:
    profile = mod_json(
        level=2,
        segments=[_seg(1, rules=[{"name": "p1"}], mapping={"override": True})],
    )
    view = _view(None, profile)
    found = _resolved(view)
    assert found
    assert {r.origin for r in found.values()} == {Origin.PROFILE, Origin.FACTORY}
    assert view.level == Resolved(Origin.PROFILE, 2, "level")
    assert view.edge_note == Resolved(Origin.FACTORY, "", "edgeNote")
    assert view.segments[0].rules == [
        Resolved(Origin.PROFILE, Rule(name="p1"), "segments[0].rules[0]")
    ]


def _owned_in(model: object) -> list[object]:
    found: list[object] = []
    for value in vars(model).values():
        if isinstance(value, Owned):
            found.append(value)
        elif isinstance(value, Layered):
            found.extend(_owned_in(value))
        elif isinstance(value, list):
            items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
            for item in items:
                if isinstance(item, Layered):
                    found.extend(_owned_in(item))
    return found


def test_the_view_is_read_only() -> None:
    view = _view({"level": 1}, {"segments": [_seg(1)]})
    with pytest.raises(TypeError, match="state.edge or state.profile"):
        view.level = Resolved(Origin.EDGE, 3, "level")  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(TypeError, match="state.edge or state.profile"):
        view.segments[0].rules = []
    assert _owned_in(view) == []


def test_the_view_does_not_share_values_with_the_layers() -> None:
    edge = Mod[Edge].model_validate(mod_json(settings={"a": 5}))
    profile = Mod[Profile].model_validate(mod_json())
    view = effective_view(edge, profile, Mod[Effective])
    view.settings.value.a = 6
    assert edge.settings.stored == Settings(a=5)


def test_effective_view_refuses_a_class_of_another_layer() -> None:
    profile = Mod[Profile].model_validate(mod_json())
    with pytest.raises(TypeError, match="Effective"):
        _ = effective_view(None, profile, Mod[Edge])


def test_effective_dump_gives_values_and_origins() -> None:
    profile = mod_json(
        level=2,
        profileNote="p",
        segments=[_seg(7, "S", rules=[{"name": "p1"}])],
    )
    edge = mod_json(
        enabled=False,
        edgeNote="e",
        segments=[_seg(9, "T"), _seg(7, "S", rules=[{"name": "e1"}])],
    )
    data, found = effective_dump(_view(edge, profile))
    assert data["enabled"] is False
    assert data["level"] == 2
    assert data["settings"] == {"a": 1, "b": False}
    assert data["pairA"] == {"a": 1, "b": False}
    assert data["pairOn"] is False
    assert data["affinity"] == {"override": False, "affinity": []}
    assert data["edgeNote"] == "e"
    assert data["profileNote"] == "p"
    # Unconverted is MISSING in the view: no key.
    assert "legacy" not in data
    segments: Any = data["segments"]
    assert [s["segment"]["segmentId"] for s in segments] == [7, 9]
    assert [r["name"] for r in segments[0]["rules"]] == ["e1", "p1"]
    assert "legacy" not in segments[0]
    assert segments[0]["mapping"] == {"override": False, "value": 0}

    # The map: one entry per Resolved, keyed by wire path, segments by id and
    # other list items by position in the view; the source keeps the stored
    # position in the layer it came from.
    assert found["enabled"] == Resolved(Origin.EDGE, False, "enabled")
    assert found["level"] == Resolved(Origin.PROFILE, 2, "level")
    assert found["pairA"].origin is Origin.FACTORY
    assert found["segments[7].rules[0]"] == Resolved(
        Origin.EDGE, Rule(name="e1"), "segments[1].rules[0]"
    )
    assert found["segments[7].rules[1]"] == Resolved(
        Origin.PROFILE, Rule(name="p1"), "segments[0].rules[0]"
    )
    assert found["segments[7].mapping"].origin is Origin.FACTORY
    assert found["segments[9].mapping"].origin is Origin.FACTORY
    assert "legacy" not in found
    assert all(isinstance(r, Resolved) for r in found.values())


def test_authority_on_a_field_that_is_not_inheritable_is_refused() -> None:
    with pytest.raises(TypeError, match=r"Bad\.flag.*Authority"):

        class Bad(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            flag: Annotated[bool, Authority(lambda value: value is False)] = True

    with pytest.raises(TypeError, match=r"Bad2\.note.*Authority"):

        class Bad2(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            note: Annotated[EdgeOnly[bool], Authority(lambda value: True)] = (
                kind_default(True)
            )
