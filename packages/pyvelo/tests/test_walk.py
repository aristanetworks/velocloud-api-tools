"""``walk_fields``: the fields of a model and of the models below it.

Each field comes with its path and its segment.
"""

from typing import Annotated

import pytest
from pydantic import Field
from pyvelo.core import (
    FieldSite,
    Key,
    KeyField,
    Path,
    Positional,
    VcoModel,
    walk_fields,
)
from pyvelo.core import walk as walk_module
from pyvelo.core.rebase import item_key


class Meta(VcoModel):
    name: str
    segment_id: int = 0


class Leaf(VcoModel):
    value: int = 0


class Big(VcoModel):
    """A class that holds no ``Leaf``: the walk is told to skip it."""

    items: list[int] = Field(default_factory=list)


class Entry(VcoModel):
    name: str
    leaf: Leaf = Field(default_factory=Leaf)


class Segment(VcoModel):
    segment: Meta
    leaf: Leaf = Field(default_factory=Leaf)
    entries: Annotated[list[Entry], KeyField("name")] = Field(default_factory=list)
    steps: Annotated[list[Leaf], Positional()] = Field(default_factory=list)
    by_name: dict[str, Leaf] = Field(default_factory=dict)
    big: Big = Field(default_factory=Big)


class Root(VcoModel):
    top: Leaf = Field(default_factory=Leaf)
    other: Annotated[list[Leaf], Positional()] = Field(default_factory=list)
    segments: Annotated[list[Segment], KeyField("segment.name")] = Field(
        default_factory=list
    )


def _holds_leaf(model_cls: type[VcoModel]) -> bool:
    return model_cls is not Big


def _sites(data: VcoModel) -> list[FieldSite]:
    return list(walk_fields(data, holds=_holds_leaf))


def _root() -> Root:
    return Root.model_validate(
        {
            "top": {"value": 1},
            "other": [{"value": 2}],
            "segments": [
                {
                    "segment": {"name": "Guest", "segment_id": 2},
                    "leaf": {"value": 3},
                    "entries": [{"name": "e1", "leaf": {"value": 4}}],
                    "steps": [{"value": 5}, {"value": 6}],
                    "by_name": {"x": {"value": 7}},
                    "big": {"items": [1, 2]},
                }
            ],
        }
    )


def _paths(sites: list[FieldSite], name: str) -> list[Path]:
    return [site.path for site in sites if site.name == name]


def test_the_fields_of_the_root_and_of_a_sub_model_are_found_with_their_paths() -> None:
    sites = _sites(_root())
    assert _paths(sites, "top") == [Path(("top",))]
    assert Path(("top", "value")) in _paths(sites, "value")


def test_a_keyed_list_names_its_items_by_key() -> None:
    paths = _paths(_sites(_root()), "name")
    assert Path(("segments", Key("Guest"), "entries", Key("e1"), "name")) in paths


def test_a_positional_list_names_its_items_by_position() -> None:
    paths = _paths(_sites(_root()), "value")
    assert Path(("other", 0, "value")) in paths
    assert Path(("segments", Key("Guest"), "steps", 1, "value")) in paths


def test_a_map_names_its_items_by_key() -> None:
    paths = _paths(_sites(_root()), "value")
    assert Path(("segments", Key("Guest"), "by_name", "x", "value")) in paths


def test_a_site_holds_the_model_the_name_and_the_field_info() -> None:
    root = _root()
    site = next(s for s in _sites(root) if s.path == Path(("top", "value")))
    assert site.model is root.top
    assert site.name == "value"
    assert site.field_info is Leaf.model_fields["value"]


def test_the_segment_is_the_enclosing_item_of_the_root_segments_list() -> None:
    root = _root()
    sites = _sites(root)
    inside = [s for s in sites if s.path.parts[:2] == ("segments", Key("Guest"))]
    assert inside
    assert all(s.segment is root.segments[0] for s in inside)
    outside = [s for s in sites if s.path.parts[0] != "segments"]
    assert outside
    assert all(s.segment is None for s in outside)


def test_the_item_of_the_segments_list_has_no_segment_for_its_own_field_in_the_root() -> (
    None
):
    sites = _sites(_root())
    own = [s for s in sites if s.path == Path(("segments",))]
    assert [s.segment for s in own] == [None]


def test_a_class_that_holds_nothing_is_not_walked() -> None:
    seen: list[type[VcoModel]] = []

    def holds(model_cls: type[VcoModel]) -> bool:
        seen.append(model_cls)
        return model_cls is not Segment

    sites = list(walk_fields(_root(), holds=holds))
    assert not any(
        s.path.parts[0] == "segments" and len(s.path.parts) > 1 for s in sites
    )
    assert Path(("top", "value")) in [s.path for s in sites]


def test_the_answer_for_a_class_is_asked_once() -> None:
    asked: list[type[VcoModel]] = []

    def holds(model_cls: type[VcoModel]) -> bool:
        asked.append(model_cls)
        return True

    data = Root.model_validate({"other": [{"value": n} for n in range(50)]})
    _ = list(walk_fields(data, holds=holds))
    assert asked.count(Leaf) == 1


def test_a_big_class_is_skipped_whole() -> None:
    sites = _sites(_root())
    assert Path(("segments", Key("Guest"), "big")) in _paths(sites, "big")
    assert not any(s.name == "items" for s in sites)


def test_a_list_of_items_that_hold_nothing_costs_no_key_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Quiet(VcoModel):
        name: str

    class Holder(VcoModel):
        leaf: Leaf = Field(default_factory=Leaf)
        quiet: Annotated[list[Quiet], KeyField("name")] = Field(default_factory=list)
        quiet_by_name: dict[str, Quiet] = Field(default_factory=dict)

    looked_up: list[object] = []
    real = item_key

    def spy(item: object, paths: tuple[str, ...]) -> object:
        looked_up.append(item)
        return real(item, paths)

    monkeypatch.setattr(walk_module, "item_key", spy)
    data = Holder.model_validate(
        {
            "leaf": {"value": 1},
            "quiet": [{"name": f"q{n}"} for n in range(3000)],
            "quiet_by_name": {f"q{n}": {"name": f"q{n}"} for n in range(3000)},
        }
    )

    sites = list(walk_fields(data, holds=lambda model_cls: model_cls is not Quiet))

    assert looked_up == []
    assert Path(("leaf", "value")) in [s.path for s in sites]
    assert not any(s.name == "name" for s in sites)


def test_a_field_filter_gives_only_the_wanted_fields_and_still_reaches_below_the_others() -> (
    None
):
    asked: list[str] = []

    def wants(field_info: object) -> bool:
        asked.append(str(field_info))
        return getattr(field_info, "annotation", None) is int

    sites = list(walk_fields(_root(), holds=_holds_leaf, fields=wants))

    assert {s.name for s in sites} == {"value", "segment_id"}
    assert Path(("segments", Key("Guest"), "by_name", "x", "value")) in [
        s.path for s in sites
    ]
    assert Path(("segments", Key("Guest"), "entries", Key("e1"), "leaf", "value")) in [
        s.path for s in sites
    ]
    assert all(s.segment is not None for s in sites if s.path.parts[0] == "segments")


def test_the_field_filter_is_asked_once_for_each_field_of_a_class() -> None:
    asked: list[str] = []

    def wants(field_info: object) -> bool:
        asked.append(str(field_info))
        return True

    data = Root.model_validate({"other": [{"value": n} for n in range(50)]})
    _ = list(walk_fields(data, holds=_holds_leaf, fields=wants))

    # Root's three fields and Leaf's one, however many leaves there are.
    assert len(asked) == len(Root.model_fields) + len(Leaf.model_fields)
