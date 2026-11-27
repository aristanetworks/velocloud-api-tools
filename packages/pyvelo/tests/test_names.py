"""A path and a value as pyvelo shows them (``pyvelo.core.names``).

A field is shown by its attribute name and an item by the text that names it
alone; what the data does not hold keeps the part as it was written.
"""

import itertools
from collections.abc import Callable, Collection, Iterator
from typing import Annotated, Literal, cast

import pytest
from layered_models import Mod, mod_json
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.device_settings.segment import (
    DnsPrimaryProvider,
    SegmentDns,
    SegmentSyslog,
)
from pyvelo.client.models.enterprise.service import SymantecWssLocationUsage
from pyvelo.client.schemas.enterprise import DnsService
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
    at,
    effective_view,
    get_key_spec,
    item_part,
    item_selector,
    kind_default,
    named_path,
    named_value,
    select_items,
    selector_of,
)
from pyvelo.core.layers import L
from pyvelo.core.names import named_change_path
from pyvelo.core.rebase import item_key


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
    ground: Annotated[int, Alias("floor")] = 0


class Kennel(VcoModel):
    floor_area: int = 0
    floor_level: Annotated[int, Alias("floor")] = 0


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


def test_a_position_no_selector_names_alone_is_kept_but_is_no_patch_key() -> None:
    """The shown position is where the item is; as a patch key it is another item.

    The entry at position 1 shares its text with another, and ``#1`` is read
    first as the entry whose text that is. So nothing names it alone.
    """
    data = Tags.model_validate({"tags": ["#1", "b", "b"]})
    assert item_part(data, "tags", 1) is None
    assert named_path(Path.parse("tags[#1]"), data) == "tags[#1]"
    assert select_items(data, "tags", "#1") == [0]


class Numbers(VcoModel):
    numbers: list[int] = Field(default_factory=list)


class Mixed(VcoModel):
    values: list[str | int] = Field(default_factory=list)


def test_a_number_entry_of_a_list_of_values_is_shown_bare() -> None:
    data = Numbers(numbers=[1, 2])
    assert item_part(data, "numbers", 1) == Key(2)
    assert named_path(Path.parse("numbers[#1]"), data) == "numbers[2]"
    mixed = Mixed(values=[7, "8"])
    assert named_path(Path.parse("values[#0]"), mixed) == "values[7]"
    assert named_path(Path.parse("values[#1]"), mixed) == 'values["8"]'


# ---------------------------------------------------------------------------
# Every shown part, over lists built from texts that read as something else
# ---------------------------------------------------------------------------

HOSTILE = (
    "#1",
    "#01",
    "#0",
    "1",
    "01",
    "true",
    "",
    "a]b",
    'say "x"',
    "Corp.Guest v2",
    "x,y",
)
"""Texts that read as a position, a number, a JSON value, or need quoting."""


def hostile_lists() -> Iterator[tuple[VcoModel, str]]:
    """Lists built from every pair of the hostile texts, and the field of each.

    A keyed list with a label, one with an alt key and a list of values. In
    the first of each a text is also another item's key (or label, or alt
    key) and two items share one; in the second a label or an alt key stands
    beside another item's key.
    """
    for a, b in itertools.product(HOSTILE, repeat=2):
        yield (
            Rule(
                name="r",
                hubs=[
                    Hub(logical_id=a, name=b),
                    Hub(logical_id=b, name=a),
                    Hub(logical_id="other", name=b),
                    Hub(logical_id="bare", name=None),
                ],
            ),
            "hubs",
        )
        yield (
            Doc(
                rules=[
                    Rule(name=a, rule_logical_id=b),
                    Rule(name=b, rule_logical_id=a),
                    Rule(name="other", rule_logical_id=b),
                    Rule(name="bare"),
                ]
            ),
            "rules",
        )
        yield (
            Rule(name="r", hubs=[Hub(logical_id=a), Hub(logical_id="k", name=b)]),
            "hubs",
        )
        yield (
            Doc(rules=[Rule(name=a), Rule(name="k", rule_logical_id=b)]),
            "rules",
        )
        yield Tags(tags=[a, b]), "tags"
        yield Tags(tags=[a, b, a, "other"]), "tags"
    yield Numbers(numbers=[1, 2, 2, 3, 0]), "numbers"
    for values in itertools.permutations([2, "2", 1, "#1", "01"], 3):
        yield Mixed(values=list(values)), "values"


def entries_of(model: VcoModel, field: str) -> list[object]:
    entries: object = getattr(model, field)
    assert isinstance(entries, list)
    return cast("list[object]", entries)


def names_alone(model: VcoModel, field: str, selector: str, position: int) -> bool:
    try:
        return select_items(model, field, selector) == [position]
    except PatchError:
        return False


def test_every_shown_part_parses_back_and_selects_its_item_and_no_other() -> None:
    shown_parts = 0
    unnamed = 0
    for model, field in hostile_lists():
        for position, entry in enumerate(entries_of(model, field)):
            where = (field, position, entry)
            part = item_part(model, field, position)
            if part is None:
                # Nothing names the item alone: not even its position, which
                # is the last thing tried.
                assert not names_alone(model, field, at(position), position), where
                unnamed += 1
                continue
            parsed = Path.parse(str(Path((field, part))))
            assert parsed.parts == (field, part), where
            assert names_alone(model, field, selector_of(part), position), where
            shown_parts += 1
    assert shown_parts > 1000
    assert unnamed > 0


def test_item_part_and_item_selector_agree_but_for_an_empty_label() -> None:
    compared = 0
    for model, field in hostile_lists():
        spec = get_key_spec(type(model).model_fields[field])
        label = None if spec is None else spec.label
        for position, entry in enumerate(entries_of(model, field)):
            if label is not None and item_key(entry, (label,)) == "":
                continue
            part = item_part(model, field, position)
            text = None if part is None else selector_of(part)
            assert text == item_selector(model, field, position), (field, entry)
            compared += 1
    assert compared > 1000


def test_a_text_that_reads_as_a_position_with_a_leading_zero_is_not_one() -> None:
    tags = Tags(tags=["#1", "#01"])
    assert item_part(tags, "tags", 1) == Key("#01")
    assert named_path(Path.parse("tags[#1]"), tags) == 'tags["#01"]'
    assert select_items(tags, "tags", "#01") == [1]
    rule = Rule(
        name="r",
        hubs=[Hub(logical_id="#1"), Hub(logical_id="h2", name="#01")],
    )
    assert item_part(rule, "hubs", 1) == Key("#01")
    assert select_items(rule, "hubs", "#01") == [1]


# ---------------------------------------------------------------------------
# A field that holds one model or a list of them
# ---------------------------------------------------------------------------


def dns_service(usage: object) -> DnsService:
    return DnsService.model_validate(
        {
            "id": 1,
            "logicalId": "00000000-0000-4000-8000-000000000001",
            "name": "dns",
            "created": "2026-01-01T00:00:00.000Z",
            "type": "dns",
            "data": {},
            "wssUsage": usage,
        }
    )


USAGE: dict[str, object] = {
    "locationCount": 2,
    "profiles": [{"id": 5, "segmentObjectId": 9}],
}


def test_a_field_that_holds_one_model_is_walked_as_that_model() -> None:
    service = dns_service(USAGE)
    assert isinstance(service.wss_usage, SymantecWssLocationUsage)
    assert named_path(Path.parse("wssUsage.locationCount"), service) == (
        "wss_usage.location_count"
    )
    assert named_path(Path.parse("wssUsage.profiles[#0].segmentObjectId"), service) == (
        "wss_usage.profiles[#0].segment_object_id"
    )


def test_the_same_field_holding_a_list_is_walked_as_a_list() -> None:
    service = dns_service([USAGE])
    assert isinstance(service.wss_usage, list)
    assert named_path(Path.parse("wssUsage[#0].locationCount"), service) == (
        "wss_usage[#0].location_count"
    )
    assert named_path(
        Path.parse("wssUsage[#0].profiles[#0].segmentObjectId"), service
    ) == ("wss_usage[#0].profiles[#0].segment_object_id")


def test_a_class_alone_takes_a_name_as_a_field_and_a_bracket_as_an_item() -> None:
    assert named_path(Path.parse("wssUsage.locationCount"), DnsService) == (
        "wss_usage.location_count"
    )
    assert named_path(Path.parse("wssUsage[#0].locationCount"), DnsService) == (
        "wss_usage[#0].location_count"
    )
    unset = dns_service(USAGE).model_copy(update={"wss_usage": MISSING})
    assert named_path(Path.parse("wssUsage.locationCount"), unset) == (
        "wss_usage.location_count"
    )


def test_a_value_is_named_by_the_shape_it_has() -> None:
    named: dict[str, object] = {
        "location_count": 2,
        "profiles": [{"id_": 5, "segment_object_id": 9}],
    }
    assert named_value({"wssUsage": USAGE}, DnsService) == {"wss_usage": named}
    assert named_value({"wssUsage": [USAGE]}, DnsService) == {"wss_usage": [named]}
    assert named_value(USAGE, DnsService, Path.parse("wssUsage")) == named
    assert named_value([USAGE], DnsService, Path.parse("wssUsage")) == [named]
    assert named_value(USAGE, DnsService, Path.parse("wssUsage[#0]")) == named


def test_one_item_found_where_a_list_is_declared_is_named_as_an_item() -> None:
    assert named_value(A, Doc, Path.parse("rules[Web].hubs")) == {
        "logical_id": "h1",
        "name": "Hub-A",
    }


PRIMARY = "deviceSettings:dns:primaryProvider"
PRIVATE = "deviceSettings:dns:privateProviders"


def test_a_dns_provider_is_walked_as_one_object_or_as_a_list() -> None:
    one = SegmentDns.model_validate(
        {
            "primaryProvider": {"ref": PRIMARY, "inlineKey": {"inlineKey": 1}},
            "backupProvider": [],
            "privateProviders": {"ref": PRIVATE},
        }
    )
    assert isinstance(one.primary_provider, DnsPrimaryProvider)
    assert named_path(Path.parse("primaryProvider.service"), one) == (
        "primary_provider.service"
    )
    assert named_path(Path.parse("primaryProvider.inlineKey.inlineKey"), one) == (
        "primary_provider.inlineKey.inlineKey"
    )
    assert named_path(Path.parse("privateProviders.ref"), one) == (
        "private_providers.ref"
    )
    listed = SegmentDns.model_validate(
        {
            "primaryProvider": [{"ref": PRIMARY}],
            "backupProvider": [],
            "privateProviders": [{"ref": PRIVATE}, {"ref": PRIVATE}],
        }
    )
    assert isinstance(listed.primary_provider, list)
    assert named_path(Path.parse("primaryProvider[#0].ref"), listed) == (
        "primary_provider[#0].ref"
    )
    assert named_path(Path.parse("privateProviders[#1].ref"), listed) == (
        "private_providers[#1].ref"
    )
    for path in ("primaryProvider.service", "primaryProvider[#0].ref"):
        assert named_path(Path.parse(path), SegmentDns) == (
            path.replace("primaryProvider", "primary_provider")
        )
    value = {"primaryProvider": {"ref": PRIMARY}, "localDNS": [{"fqdn": "a.test"}]}
    assert named_value(value, SegmentDns) == {
        "primary_provider": {"ref": PRIMARY},
        "local_dns": [{"fqdn": "a.test"}],
    }
    assert named_value({"primaryProvider": [{"ref": PRIMARY}]}, SegmentDns) == {
        "primary_provider": [{"ref": PRIMARY}]
    }


class Lodge(VcoModel):
    home: Den | list[Kennel] | None = None
    floors: dict[str, list[Den]] = Field(default_factory=dict)


def test_a_class_alone_keeps_a_name_the_one_model_and_the_items_name_differently() -> (
    None
):
    """``floor`` is ``Den.ground`` and ``Kennel.floor_level``: the class cannot say."""
    assert named_path(Path.parse("home.floor.below"), Lodge) == "home.floor.below"
    assert named_path(Path.parse("home.floorSize"), Lodge) == "home.floor_size"
    assert named_path(Path.parse("home[#0].floor"), Lodge) == "home[#0].floor_level"
    assert named_path(Path.parse("home[#0].floorArea"), Lodge) == (
        "home[#0].floor_area"
    )
    one = Lodge.model_validate({"home": {"floor": 2}})
    assert named_path(Path.parse("home.floor"), one) == "home.ground"
    listed = Lodge.model_validate({"home": [{"floor": 2}]})
    assert named_path(Path.parse("home[#0].floor"), listed) == "home[#0].floor_level"
    assert named_value({"floor": 1}, Lodge, Path.parse("home")) == {"ground": 1}
    assert named_value([{"floor": 1}], Lodge, Path.parse("home")) == [
        {"floor_level": 1}
    ]


def test_a_key_of_a_map_of_lists_is_data_and_is_not_renamed() -> None:
    data = Lodge.model_validate({"floors": {"floorSize": [{"floorSize": 3}]}})
    path = Path.parse("floors.floorSize[#0].floorSize")
    assert named_path(path, data) == path
    assert named_path(path, Lodge) == path
    value = {"floors": {"floorSize": [{"floorSize": 3}]}}
    assert named_value(value, Lodge) == value


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
# The path of a change: its items are named as read, and shown as edited
# ---------------------------------------------------------------------------


def rule_row(name: str, rule_id: str | None, *hubs: dict[str, object]) -> object:
    return {"name": name, "ruleLogicalId": rule_id, "hubs": list(hubs)}


def rules_doc(*rows: object) -> Doc:
    return Doc.model_validate({"rules": list(rows)})


def changed(text: str, read: Doc, edited: Doc, *, removed: bool = False) -> str:
    return str(named_change_path(Path.parse(text), read, edited, removed=removed))


def test_a_change_inside_a_renamed_item_is_shown_under_its_new_name() -> None:
    read = rules_doc(rule_row("Web", "r1", A))
    edited = rules_doc(rule_row("Web2", "r1", A))
    assert changed("rules[Web].hubs[h1].logicalId", read, edited) == (
        "rules[Web2].hubs[Hub-A].logical_id"
    )
    # ``named_path`` alone finds no rule ``Web`` in the data as edited.
    assert shown("rules[Web].hubs[h1].logicalId", edited) == (
        "rules[Web].hubs[h1].logical_id"
    )


def test_items_that_swap_names_are_paired_by_their_alt_key() -> None:
    read = rules_doc(rule_row("One", "r1"), rule_row("Two", "r2"))
    edited = rules_doc(rule_row("Two", "r1"), rule_row("One", "r2"))
    assert changed("rules[One].name", read, edited) == "rules[Two].name"
    assert changed("rules[Two].name", read, edited) == "rules[One].name"


def test_items_without_an_alt_key_are_paired_by_their_key() -> None:
    read = rules_doc(rule_row("Web", None, A))
    edited = rules_doc(rule_row("Other", None), rule_row("Web", None, A))
    assert changed("rules[#0].hubs[h1].name", read, edited) == (
        "rules[Web].hubs[Hub-A].name"
    )


def test_a_removed_item_is_shown_as_read_below_what_is_shown_as_edited() -> None:
    read = rules_doc(rule_row("Web", "r1", A, B))
    edited = rules_doc(rule_row("Web2", "r1", B))
    assert changed("rules[Web].hubs[h1]", read, edited, removed=True) == (
        "rules[Web2].hubs[Hub-A]"
    )
    assert changed("rules[Web].hubs[h1].logicalId", read, edited, removed=True) == (
        "rules[Web2].hubs[Hub-A].logical_id"
    )
    # An item both have is shown as edited, whatever the change does below it.
    assert changed("rules[Web].hubs[h2].name", read, edited, removed=True) == (
        "rules[Web2].hubs[h2].name"
    )


def test_an_item_with_no_partner_is_read_in_the_data_the_change_says() -> None:
    """``X`` as read is removed, and ``Web`` is renamed to ``X``."""
    read = rules_doc(
        rule_row("Web", "r1", {"logicalId": "h1", "name": "New"}),
        rule_row("X", "r2", {"logicalId": "h1", "name": "Old"}),
    )
    edited = rules_doc(rule_row("X", "r1", {"logicalId": "h1", "name": "New"}))
    assert changed("rules[X].hubs[h1]", read, edited, removed=True) == (
        "rules[X].hubs[Old]"
    )
    assert changed("rules[X].hubs[h1]", read, edited) == "rules[X].hubs[New]"
    assert changed("rules[Web].hubs[h1]", read, edited) == "rules[X].hubs[New]"


def test_an_added_item_is_found_in_the_data_as_edited() -> None:
    read = rules_doc(rule_row("Web", "r1"))
    edited = rules_doc(rule_row("Web", "r1", A))
    assert changed("rules[Web].hubs[h1]", read, edited) == "rules[Web].hubs[Hub-A]"


def test_a_part_neither_document_has_is_kept_and_the_fields_below_are_named() -> None:
    read = rules_doc(rule_row("Web", "r1"))
    for removed in (False, True):
        assert changed("rules[Nope].ruleLogicalId", read, read, removed=removed) == (
            "rules[Nope].rule_logical_id"
        )
        assert changed("nope.ruleLogicalId", read, read, removed=removed) == (
            "nope.ruleLogicalId"
        )


def test_an_item_of_a_list_without_a_key_has_no_partner() -> None:
    read = Doc.model_validate({"plain": [A, B]})
    edited = Doc.model_validate({"plain": [B]})
    assert changed("plain[#1].logicalId", read, edited, removed=True) == (
        "plain[#1].logical_id"
    )
    assert changed("plain[#0].logicalId", read, edited) == "plain[#0].logical_id"


def test_an_entry_of_a_map_is_its_key_in_both_documents() -> None:
    read = shelf()
    edited = Shelf.model_validate({"hubs": {"top": A}})
    path = Path.parse('hubs."low:1".logicalId')
    assert named_change_path(path, read, edited, removed=True) == (
        'hubs."low:1".logical_id'
    )
    assert named_change_path(Path.parse("hubs.top.logicalId"), read, edited) == (
        "hubs.top.logical_id"
    )


class Mac(VcoModel):
    mac_address: str
    note: str = ""


class Macs(VcoModel):
    macs: Annotated[list[Mac], KeyField("mac_address", fold_case=True)] = Field(
        default_factory=list
    )


def test_keys_that_differ_by_letter_case_are_one_where_the_list_folds_case() -> None:
    read = Macs.model_validate({"macs": [{"macAddress": "AA:BB"}]})
    edited = Macs.model_validate({"macs": [{"macAddress": "aa:bb"}]})
    path = Path(("macs", Key("AA:BB"), "note"))
    assert named_change_path(path, read, edited, removed=True) == 'macs["aa:bb"].note'


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
