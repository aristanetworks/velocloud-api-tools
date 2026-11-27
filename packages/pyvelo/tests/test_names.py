"""A path and a value as pyvelo shows them (``pyvelo.core.names``).

A field is shown by its attribute name and an item by the text that names it
alone; what the data does not hold keeps the part as it was written.
"""

from collections.abc import Callable, Collection
from typing import Annotated, Literal

import pytest
from layered_models import Mod, mod_json
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.device_settings.segment import SegmentSyslog
from pyvelo.core import (
    Alias,
    Edge,
    Effective,
    Key,
    KeyField,
    Layered,
    NameOf,
    PatchError,
    Path,
    Profile,
    ProfileOnly,
    Resolved,
    ResolverNotLoadedError,
    SegmentIdentity,
    VcoModel,
    effective_view,
    item_part,
    item_selector,
    kind_default,
    named_path,
    named_value,
    select_items,
    selector_of,
)
from pyvelo.core.layers import L


class Hub(VcoModel):
    logical_id: str
    name: str | None = None


class Rule(VcoModel):
    name: str
    rule_logical_id: str | None = None
    hubs: Annotated[list[Hub], KeyField("logical_id", label="name")] = Field(
        default_factory=list
    )


class Doc(VcoModel):
    rules: Annotated[list[Rule], KeyField("name", alt="rule_logical_id")] = Field(
        default_factory=list
    )
    plain: list[Hub] = Field(default_factory=list)


def doc(*hubs: dict[str, object], rule: str = "Web") -> Doc:
    return Doc.model_validate(
        {"rules": [{"name": rule, "ruleLogicalId": "r1", "hubs": list(hubs)}]}
    )


A: dict[str, object] = {"logicalId": "h1", "name": "Hub-A"}
B: dict[str, object] = {"logicalId": "h2"}


def shown(text: str, data: Doc) -> str:
    return str(named_path(Path.parse(text), data))


def test_fields_by_attribute_and_items_by_name() -> None:
    assert shown("rules[Web].hubs[h1].logicalId", doc(A, B)) == (
        "rules[Web].hubs[Hub-A].logical_id"
    )


def test_an_item_without_its_label_is_shown_by_its_key() -> None:
    assert shown("rules[Web].hubs[h2]", doc(A, B)) == "rules[Web].hubs[h2]"


def test_a_position_is_shown_by_name_where_the_item_has_one() -> None:
    assert shown("rules[#0].hubs[#1]", doc(A, B)) == "rules[Web].hubs[h2]"


def test_an_item_the_data_lacks_keeps_the_part_written() -> None:
    assert shown("rules[Nope].ruleLogicalId", doc(A)) == "rules[Nope].rule_logical_id"


def test_a_class_alone_converts_the_field_names() -> None:
    assert named_path(Path.parse("rules[Web].hubs[h1].logicalId"), Doc) == (
        "rules[Web].hubs[h1].logical_id"
    )


def test_a_shared_label_falls_back_to_the_key() -> None:
    twins = doc(A, {"logicalId": "h2", "name": "Hub-A"})
    assert shown("rules[Web].hubs[h2]", twins) == "rules[Web].hubs[h2]"


def test_a_label_that_reads_as_a_position_is_not_used() -> None:
    odd = doc({"logicalId": "h1", "name": "#1"}, B)
    assert shown("rules[Web].hubs[h1]", odd) == "rules[Web].hubs[h1]"


def test_a_text_key_that_reads_as_a_number_is_quoted() -> None:
    data = doc(A, rule="24")
    assert shown("rules[#0].name", data) == 'rules["24"].name'
    assert Path.parse('rules["24"].name').parts == ("rules", Key("24"), "name")


def test_a_list_without_a_key_is_shown_by_position() -> None:
    data = Doc.model_validate({"plain": [A]})
    assert shown("plain[#0].logicalId", data) == "plain[#0].logical_id"


def test_every_shown_path_parses_to_itself() -> None:
    for text in ("rules[Web].hubs[h1]", "rules[#0].hubs[#1].name", "plain[#0]"):
        path = named_path(Path.parse(text), doc(A, B))
        assert Path.parse(str(path)) == path
        assert Path.parse(str(path)).parts == path.parts


def test_an_object_is_written_in_names() -> None:
    value = {"logicalId": "h1", "name": "Hub-A", "x-extra": {"logicalId": 1}}
    assert named_value(value, Doc, Path.parse("rules[Web].hubs[h1]")) == {
        "logical_id": "h1",
        "name": "Hub-A",
        "x-extra": {"logicalId": 1},
    }
    assert value["logicalId"] == "h1"


def test_a_list_of_objects_and_a_scalar() -> None:
    assert named_value([A], Doc, Path.parse("rules[Web].hubs")) == [
        {"logical_id": "h1", "name": "Hub-A"}
    ]
    assert named_value("edge2Any", Doc, Path.parse("rules[Web].name")) == "edge2Any"
    assert named_value({"ruleLogicalId": "r"}, Doc, Path.parse("rules[Web]")) == {
        "rule_logical_id": "r"
    }


def test_item_part_keeps_the_type_of_a_key() -> None:
    data = doc(A, B)
    assert item_part(data.rules[0], "hubs", 0) == Key("Hub-A")
    assert item_part(data.rules[0], "hubs", 1) == Key("h2")
    assert item_part(Doc.model_validate({"plain": [A]}), "plain", 0) == 0


# ---------------------------------------------------------------------------
# An item whose label is the empty text
# ---------------------------------------------------------------------------


def test_a_label_that_is_the_empty_text_is_not_shown() -> None:
    data = doc(A, {"logicalId": "h2", "name": ""})
    rule = data.rules[0]
    assert item_selector(rule, "hubs", 1) == ""
    assert item_part(rule, "hubs", 1) == Key("h2")
    assert shown("rules[Web].hubs[#1].name", data) == "rules[Web].hubs[h2].name"
    assert shown('rules[Web].hubs[""]', data) == "rules[Web].hubs[h2]"


def test_two_items_with_the_empty_label_are_each_shown_by_their_key() -> None:
    data = doc({"logicalId": "h1", "name": ""}, {"logicalId": "h2", "name": ""})
    assert item_part(data.rules[0], "hubs", 0) == Key("h1")
    assert item_part(data.rules[0], "hubs", 1) == Key("h2")


# ---------------------------------------------------------------------------
# Where there is no item, and where a list is not one of models
# ---------------------------------------------------------------------------


def test_item_part_of_no_item_and_of_no_list_raises_as_item_selector_does() -> None:
    data = doc(A)
    with pytest.raises(IndexError, match="no item at position 3"):
        _ = item_part(data.rules[0], "hubs", 3)
    with pytest.raises(TypeError, match=r"Rule\.name is not a list"):
        _ = item_part(data.rules[0], "name", 0)


def test_a_bare_name_after_a_list_is_a_selector() -> None:
    assert shown("rules.Web.hubs.h1.logicalId", doc(A, B)) == (
        "rules[Web].hubs[Hub-A].logical_id"
    )
    assert shown("rules.Nope.ruleLogicalId", doc(A)) == "rules.Nope.rule_logical_id"
    assert named_path(Path.parse("rules.Web.ruleLogicalId"), Doc) == (
        "rules.Web.rule_logical_id"
    )


def test_a_position_the_list_does_not_reach_is_kept() -> None:
    assert shown("rules[#4].hubs[#0].logicalId", doc(A)) == (
        "rules[#4].hubs[#0].logical_id"
    )


def test_a_key_the_model_does_not_declare_is_kept_and_nothing_below_is_renamed() -> (
    None
):
    assert shown("rules[Web].extraKey.logicalId", doc(A)) == (
        "rules[Web].extraKey.logicalId"
    )


def test_a_path_with_no_data_and_no_class_is_returned_as_it_is() -> None:
    path = Path.parse("rules[Web].hubs[h1].logicalId")
    assert named_path(path, None) == path
    assert named_path(path, None).parts == path.parts


def test_a_field_that_is_unset_is_walked_by_its_class() -> None:
    class Outer(VcoModel):
        inner: Doc | None = None

    assert named_path(Path.parse("inner.rules[Web].ruleLogicalId"), Outer()) == (
        "inner.rules[Web].rule_logical_id"
    )


# ---------------------------------------------------------------------------
# A composite key
# ---------------------------------------------------------------------------


def test_a_composite_key_keeps_its_typed_parts() -> None:
    syslog = SegmentSyslog.model_validate(
        {
            "collectors": [
                {"host": "192.0.2.1", "protocol": "UDP", "port": 514},
                {"host": "192.0.2.1", "protocol": "TCP", "port": 514},
            ]
        }
    )
    assert item_part(syslog, "collectors", 1) == Key(("192.0.2.1", "TCP", 514))
    path = named_path(Path.parse("collectors[#0].sourceInterface"), syslog)
    assert path == 'collectors["192.0.2.1",UDP,514].source_interface'
    assert Path.parse(str(path)).parts == path.parts
    assert named_path(path, syslog).parts == path.parts


# ---------------------------------------------------------------------------
# A lookup that fails
# ---------------------------------------------------------------------------


class Link(VcoModel):
    internal_id: str
    family: str = "IPv4"


class Links(VcoModel):
    links: Annotated[list[Link], KeyField("internal_id", "family")] = Field(
        default_factory=list
    )


def test_a_selector_with_two_readings_keeps_the_part_written() -> None:
    data = Links.model_validate(
        {
            "links": [
                {"internalId": "x", "family": "true"},
                {"internalId": "x", "family": "True"},
            ]
        }
    )
    with pytest.raises(PatchError, match="is ambiguous"):
        _ = select_items(data, "links", "x,true")
    path = Path(("links", Key("x,true"), "internalId"))
    assert named_path(path, data) == 'links["x,true"].internal_id'


class Named(VcoModel):
    name: Annotated[str, NameOf(SegmentIdentity)]
    segment_id: int = 0


class Segments(VcoModel):
    segments: Annotated[list[Named], KeyField("name")] = Field(default_factory=list)


class NotLoaded:
    """Resolvers with no table loaded: every lookup says so."""

    def lookup[T](self, key: type[T], name: str, /) -> T:
        del name
        raise ResolverNotLoadedError(key)

    def lookup_by[T](
        self, identity_type: type[T], accessor: Callable[[T], object], value: object, /
    ) -> T:
        del accessor, value
        raise ResolverNotLoadedError(identity_type)

    def resolver_for(self, identity_type: type[object], /) -> object | None:
        del identity_type
        return None

    def identities[T](self, identity_type: type[T], /) -> Collection[T]:
        del identity_type
        return ()


def test_a_name_whose_lookup_is_not_loaded_keeps_the_part_written() -> None:
    data = Segments.model_validate({"segments": [{"name": "Corp"}]})
    resolvers = NotLoaded()
    with pytest.raises(ResolverNotLoadedError):
        _ = select_items(data, "segments", "Nope", resolvers)
    assert named_path(
        Path.parse("segments[Nope].segmentId"), data, resolvers=resolvers
    ) == ("segments[Nope].segment_id")
    assert named_path(
        Path.parse("segments[#0].segmentId"), data, resolvers=resolvers
    ) == ("segments[Corp].segment_id")


# ---------------------------------------------------------------------------
# A union of models
# ---------------------------------------------------------------------------


class Den(VcoModel):
    floor_size: int = 0


class Kennel(VcoModel):
    floor_area: int = 0


class Cat(VcoModel):
    kind: Literal["cat"] = "cat"
    reach: Annotated[int, Alias("size")] = 0
    claw_length: int = 0
    den: Annotated[Den, Alias("home")] = Field(default_factory=Den)


class Dog(VcoModel):
    kind: Literal["dog"] = "dog"
    girth: Annotated[int, Alias("size")] = 0
    tail_length: int = 0
    kennel: Annotated[Kennel, Alias("home")] = Field(default_factory=Kennel)


class Zoo(VcoModel):
    animals: list[Cat | Dog] = Field(default_factory=list)


def test_a_union_item_is_named_by_the_variant_the_data_holds() -> None:
    zoo = Zoo.model_validate({"animals": [{"kind": "dog", "size": 3}]})
    assert isinstance(zoo.animals[0], Dog)
    assert named_path(Path.parse("animals[#0].size"), zoo) == "animals[#0].girth"


def test_a_union_without_data_is_named_by_the_first_variant_with_the_key() -> None:
    assert named_path(Path.parse("animals[#0].size"), Zoo) == "animals[#0].reach"
    assert named_path(Path.parse("animals[#0].tailLength"), Zoo) == (
        "animals[#0].tail_length"
    )
    assert named_value(
        {"size": 1, "tailLength": 2}, Zoo, Path.parse("animals[#0]")
    ) == {"reach": 1, "tail_length": 2}


def test_below_a_key_two_variants_name_differently_only_the_first_is_walked() -> None:
    assert named_path(Path.parse("animals[#0].home.floorSize"), Zoo) == (
        "animals[#0].den.floor_size"
    )
    assert named_path(Path.parse("animals[#0].home.floorArea"), Zoo) == (
        "animals[#0].den.floorArea"
    )
    zoo = Zoo.model_validate({"animals": [{"kind": "dog"}]})
    assert named_path(Path.parse("animals[#0].home.floorArea"), zoo) == (
        "animals[#0].kennel.floor_area"
    )


# ---------------------------------------------------------------------------
# A list of values
# ---------------------------------------------------------------------------


class Tags(VcoModel):
    tags: list[str] = Field(default_factory=list)


def test_an_entry_of_a_list_of_values_is_shown_by_its_text() -> None:
    data = Tags.model_validate({"tags": ["#1", "b", "b"]})
    assert item_part(data, "tags", 0) == Key("#1")
    assert item_part(data, "tags", 2) == 2
    assert named_path(Path.parse("tags[#0]"), data) == 'tags["#1"]'
    assert select_items(data, "tags", selector_of(Key("#1"))) == [0]
    assert named_path(Path.parse("tags[#1]"), data) == "tags[#1]"


# ---------------------------------------------------------------------------
# A map of models
# ---------------------------------------------------------------------------


class Shelf(VcoModel):
    hubs: dict[str, Hub] = Field(default_factory=dict)


def shelf() -> Shelf:
    return Shelf.model_validate({"hubs": {"top": A, "low:1": B}})


def test_an_entry_of_a_map_is_shown_by_its_key_after_a_dot() -> None:
    assert named_path(Path.parse("hubs.top.logicalId"), shelf()) == (
        "hubs.top.logical_id"
    )
    assert named_path(Path.parse("hubs[top].logicalId"), shelf()) == (
        "hubs.top.logical_id"
    )
    assert named_path(Path.parse('hubs["low:1"].logicalId'), shelf()) == (
        'hubs."low:1".logical_id'
    )


def test_a_key_of_a_map_is_never_renamed() -> None:
    data = Shelf.model_validate({"hubs": {"logicalId": A}})
    assert named_path(Path.parse("hubs.logicalId.logicalId"), data) == (
        "hubs.logicalId.logical_id"
    )
    assert named_path(Path.parse("hubs.logicalId.logicalId"), Shelf) == (
        "hubs.logicalId.logical_id"
    )


def test_an_entry_the_map_lacks_keeps_the_part_written() -> None:
    assert named_path(Path.parse("hubs[gone].logicalId"), shelf()) == (
        "hubs[gone].logical_id"
    )
    assert named_path(Path.parse("hubs[top].logicalId"), Shelf) == (
        "hubs[top].logical_id"
    )


def test_a_map_of_objects_is_written_in_names_below_its_keys() -> None:
    value = {"logicalId": {"logicalId": "h1"}}
    assert named_value(value, Shelf, Path.parse("hubs")) == {
        "logicalId": {"logical_id": "h1"}
    }


def test_an_entry_of_a_map_has_no_item_part() -> None:
    with pytest.raises(TypeError, match=r"Shelf\.hubs is a map.*use the key"):
        _ = item_part(shelf(), "hubs", 0)


# ---------------------------------------------------------------------------
# The effective view, whose lists hold wrappers and not models
# ---------------------------------------------------------------------------


def test_a_list_of_wrapped_items_keeps_its_positions() -> None:
    segment = {
        "segment": {"segmentId": 1, "name": "Guest"},
        "rules": [{"name": "Web"}, {"name": "Mail"}],
    }
    profile = Mod[Profile].model_validate(mod_json(segments=[segment]))
    edge = Mod[Edge].model_validate(mod_json(segments=[segment]))
    view = effective_view(edge, profile, Mod[Effective])
    assert isinstance(view.segments[0].rules[1], Resolved)
    assert named_path(Path.parse("segments[#0].rules[#1].action"), view) == (
        "segments[Guest].rules[#1].action"
    )
    assert item_part(view.segments[0], "rules", 1) == 1


class Whole(Layered[L]):
    defaults: Annotated[ProfileOnly[list[Rule]], KeyField("name")] = kind_default(
        factory=list
    )


def test_a_list_the_view_holds_as_one_wrapped_value_keeps_its_positions() -> None:
    profile = Whole[Profile].model_validate({"defaults": [{"name": "Web"}]})
    assert named_path(Path.parse("defaults[#0].ruleLogicalId"), profile) == (
        "defaults[Web].rule_logical_id"
    )
    view = effective_view(None, profile, Whole[Effective])
    assert isinstance(view.defaults, Resolved)
    assert named_path(Path.parse("defaults[#0].ruleLogicalId"), view) == (
        "defaults[#0].rule_logical_id"
    )
    with pytest.raises(IndexError, match="no item at position 0"):
        _ = item_part(view, "defaults", 0)


# ---------------------------------------------------------------------------
# A value
# ---------------------------------------------------------------------------


def test_a_value_with_no_model_and_a_missing_one_are_returned_as_they_are() -> None:
    assert named_value(MISSING, Doc, Path.parse("rules[Web].name")) is MISSING
    assert named_value({"logicalId": 1}, None) == {"logicalId": 1}
    assert named_value({"rules": [{"ruleLogicalId": "r"}]}, Doc) == {
        "rules": [{"rule_logical_id": "r"}]
    }


def test_a_value_is_copied_at_every_depth() -> None:
    value = {"x-extra": {"deep": [1, {"logicalId": 2}]}}
    named = named_value(value, Doc, Path.parse("rules[Web].hubs[h1]"))
    assert named == value
    assert isinstance(named, dict)
    assert named["x-extra"] is not value["x-extra"]
    assert named["x-extra"]["deep"] is not value["x-extra"]["deep"]


def test_two_keys_of_one_field_are_each_kept_as_stored() -> None:
    value = {"logicalId": "a", "logical_id": "b", "name": "n"}
    assert named_value(value, Doc, Path.parse("rules[Web].hubs[h1]")) == value


# ---------------------------------------------------------------------------
# PatchError.at
# ---------------------------------------------------------------------------


def test_a_patch_error_at_another_path_is_the_same_error_there() -> None:
    cause = ValueError("why")
    error = PatchError("no item has that key", Path.parse("rules[#0].hubs"), "no_item")
    error.__cause__ = cause
    moved = error.at(Path.parse("rules[Web].hubs"))
    assert str(moved) == "rules[Web].hubs: no item has that key"
    assert (moved.message, moved.kind) == ("no item has that key", "no_item")
    assert moved.path.parts == ("rules", Key("Web"), "hubs")
    assert moved.__cause__ is cause
    assert moved is not error
    assert error.path == "rules[#0].hubs"


def test_a_patch_error_with_no_cause_has_none_at_another_path() -> None:
    error = PatchError("no", Path(("a",)), "address")
    moved = error.at(Path(("b",)))
    assert moved.__cause__ is None
    assert moved.__suppress_context__ is False
