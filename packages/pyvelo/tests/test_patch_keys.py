"""Keys in a patch: alt keys, duplicate keys, composite keys, labels and ``KeySpec``."""

import copy
import json
import pickle
import uuid
from typing import Annotated, Any

import pytest
from pydantic import MISSING, Field, ValidationError
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.root import ModelInterfaces
from pyvelo.client.models.configuration.device_settings.segment import (
    SegmentRoutes,
    SegmentSyslog,
    StaticRoute,
)
from pyvelo.client.models.configuration.wan.link import WanLink
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.core import (
    KeyField,
    KeySpec,
    PatchError,
    VcoModel,
    add,
    at,
    delete,
    move,
)
from pyvelo.core.base import list_item_type
from pyvelo.core.fields import get_key_spec
from pyvelo.core.merge import select_items
from pyvelo.core.rebase import (
    AddedItem,
    Key,
    KeyedList,
    Path,
    item_key,
    key_text,
    node_child,
    pair_items,
    python_path,
)

from .patch_models import Link, Root, Route, Rule


def two() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="b", rule_id="i2")])


def two_named_a() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="a", rule_id="i2")])


def names(root: Root) -> list[str]:
    return [rule.name for rule in root.rules]


def test_select_by_alt_key() -> None:
    root = two()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]

    root = two()
    _ = root.merge_patch({"rules": {"i1": delete()}})
    assert names(root) == ["b"]

    root = two()
    _ = root.merge_patch({"rules": {"a": move(after="i2")}})
    assert names(root) == ["b", "a"]


def test_key_wins_over_another_items_alt_key() -> None:
    root = Root(rules=[Rule(name="a", rule_id="b"), Rule(name="b", rule_id="i2")])
    _ = root.merge_patch({"rules": {"b": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_add_takes_the_key_not_the_alt_key() -> None:
    root = two()
    _ = root.merge_patch({"rules": {"i9": add()}})
    assert names(root) == ["a", "b", "i9"]
    assert root.rules[2].rule_id is MISSING


def test_duplicate_names_select_by_alt_key() -> None:
    root = two_named_a()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_duplicate_names_raise_and_list_alt_keys() -> None:
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": {"priority": 5}}})
    assert exc.value.path == Path.parse("rules[a]")
    assert "i1" in str(exc.value)
    assert "i2" in str(exc.value)
    assert [r.priority for r in root.rules] == [0, 0]


def test_add_of_a_duplicated_key_raises() -> None:
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": add()}})
    assert exc.value.path == Path.parse("rules[a]")
    assert len(root.rules) == 2


def test_keyfield_needs_a_path() -> None:
    with pytest.raises(TypeError):
        _ = KeyField()


class Keyed(VcoModel):
    one: Annotated[list[Rule], KeyField("a")] = Field(default_factory=list)
    two: Annotated[list[Rule], KeyField("a", "b")] = Field(default_factory=list)
    alt: Annotated[list[Rule], KeyField("a", alt="i", label="n")] = Field(
        default_factory=list
    )
    none: list[Rule] = Field(default_factory=list)


def test_key_spec() -> None:
    fields = Keyed.model_fields
    assert get_key_spec(fields["one"]) == KeySpec(("a",))
    two_spec = get_key_spec(fields["two"])
    assert two_spec is not None
    assert two_spec.paths == ("a", "b")
    alt_spec = get_key_spec(fields["alt"])
    assert alt_spec is not None
    assert alt_spec.alt == "i"
    assert alt_spec.label == "n"
    assert get_key_spec(fields["none"]) is None


def test_add_under_another_items_alt_key_raises() -> None:
    root = Root(rules=[Rule(name="a", rule_id="i1")])
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"i1": add()}})
    assert exc.value.path == Path.parse("rules[i1]")
    assert names(root) == ["a"]


def test_key_and_alt_selectors_for_one_item_raise() -> None:
    root = Root(rules=[Rule(name="a", rule_id="i1")])
    with pytest.raises(PatchError):
        _ = root.merge_patch({"rules": {"a": {"priority": 1}, "i1": delete()}})
    assert names(root) == ["a"]
    assert root.rules[0].priority == 0


def ids(root: Root) -> list[tuple[str, object]]:
    return [(rule.name, rule.rule_id) for rule in root.rules]


def test_a_change_cannot_give_an_item_another_items_alt_key() -> None:
    root = two()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"b": {"rule_id": "i1"}}})
    assert exc.value.path == Path.parse("rules[b]")
    assert "item 'a' already has the alt key 'i1'" in exc.value.message
    assert "another alt key" in exc.value.message
    assert ids(root) == [("a", "i1"), ("b", "i2")]


def test_alt_keys_may_be_swapped_in_one_patch() -> None:
    root = two()
    _ = root.merge_patch({"rules": {"a": {"rule_id": "i2"}, "b": {"rule_id": "i1"}}})
    assert ids(root) == [("a", "i2"), ("b", "i1")]


def test_an_add_cannot_bring_an_alt_key_that_is_in_the_list() -> None:
    root = two()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"c": add({"rule_id": "i1"})}})
    assert exc.value.path == Path.parse("rules[c]")
    assert "'i1'" in exc.value.message
    assert "item 'a'" in exc.value.message
    assert "another alt key" in exc.value.message
    assert ids(root) == [("a", "i1"), ("b", "i2")]
    with pytest.raises(PatchError) as exc:
        _ = two().merge_patch(
            {"rules": {"c": add({"rule_id": "i9"}), "d": add({"rule_id": "i9"})}}
        )
    assert exc.value.path == Path.parse("rules[d]")


def test_an_add_may_take_an_alt_key_the_same_patch_frees() -> None:
    root = two()
    _ = root.merge_patch(
        {"rules": {"a": {"rule_id": "i8"}, "c": add({"rule_id": "i1"})}}
    )
    assert ids(root) == [("a", "i8"), ("b", "i2"), ("c", "i1")]
    root = two()
    _ = root.merge_patch({"rules": {"a": delete(), "c": add({"rule_id": "i1"})}})
    assert ids(root) == [("b", "i2"), ("c", "i1")]


def test_a_change_cannot_take_an_alt_key_that_a_delete_in_the_same_patch_frees() -> (
    None
):
    # Selectors are resolved against the list as it was, as for a rename.
    root = two()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": delete(), "b": {"rule_id": "i1"}}})
    assert exc.value.path == Path.parse("rules[b]")
    assert ids(root) == [("a", "i1"), ("b", "i2")]


def test_a_list_value_cannot_repeat_an_alt_key() -> None:
    root = two()
    value = [{"name": "x", "rule_id": "i"}, {"name": "y", "rule_id": "i"}]
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": value})
    assert exc.value.path == Path.parse("rules")
    assert "#0" in exc.value.message
    assert "#1" in exc.value.message
    assert "item 'x' holds it already" in exc.value.message
    assert "give item #1 another alt key" in exc.value.message
    assert ids(root) == [("a", "i1"), ("b", "i2")]


def test_items_read_with_one_alt_key_can_still_be_changed() -> None:
    root = Root(rules=[Rule(name="a", rule_id="i"), Rule(name="b", rule_id="i")])
    _ = root.merge_patch({"rules": {"a": {"priority": 1}, "c": add()}})
    assert [r.priority for r in root.rules] == [1, 0, 0]


# --- composite keys and labels -------------------------------------------------

R1_KEY = ("10.0.0.0", 24, "192.168.1.1", "GE3")
R2_KEY = ("10.1.0.0", 16, "192.168.1.1", "")


def routes() -> Root:
    return Root(
        routes=[
            Route(dest="10.0.0.0", prefix=24, gw="192.168.1.1", intf="GE3"),
            Route(dest="10.1.0.0", prefix=16, gw="192.168.1.1"),
        ]
    )


def links() -> Root:
    return Root(
        links=[
            Link(internal_id="u1", name="Internet"),
            Link(internal_id="u2", name="Internet"),
            Link(internal_id="u3", name="MPLS"),
        ]
    )


def costs(root: Root) -> list[int]:
    return [route.cost for route in root.routes]


def test_select_by_tuple() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {R1_KEY: {"cost": 5}}})
    assert costs(root) == [5, 0]


def test_select_by_string_form() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {'"10.0.0.0",24,"192.168.1.1",GE3': {"cost": 5}}})
    assert costs(root) == [5, 0]


def test_parts_compare_as_strings() -> None:
    root = routes()
    _ = root.merge_patch(
        {"routes": {("10.0.0.0", "24", "192.168.1.1", "GE3"): {"cost": 5}}}
    )
    assert costs(root) == [5, 0]


def test_empty_string_is_a_key_part() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {R2_KEY: {"cost": 5}}})
    assert costs(root) == [0, 5]

    root = routes()
    _ = root.merge_patch({"routes": {'"10.1.0.0",16,"192.168.1.1",""': {"cost": 5}}})
    assert costs(root) == [0, 5]


def test_add_by_composite_key_writes_the_parts() -> None:
    root = routes()
    _ = root.merge_patch(
        {"routes": {("10.2.0.0", 24, "192.168.1.1", "GE4"): add({"cost": 1})}}
    )
    new = root.routes[2]
    assert (new.dest, new.gw, new.intf, new.cost) == (
        "10.2.0.0",
        "192.168.1.1",
        "GE4",
        1,
    )
    assert new.prefix == 24

    root = routes()
    _ = root.merge_patch(
        {"routes": {("10.2.0.0", "24", "192.168.1.1", "GE4"): add({"cost": 1})}}
    )
    new = root.routes[2]
    assert (new.dest, new.gw, new.intf, new.cost) == (
        "10.2.0.0",
        "192.168.1.1",
        "GE4",
        1,
    )
    assert new.prefix == "24"


def test_delete_and_anchor_by_composite_key() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {R1_KEY: delete()}})
    assert [route.dest for route in root.routes] == ["10.1.0.0"]

    root = routes()
    _ = root.merge_patch({"routes": {R2_KEY: move(before=R1_KEY)}})
    assert [route.dest for route in root.routes] == ["10.1.0.0", "10.0.0.0"]


def test_wrong_number_of_parts_names_no_item() -> None:
    root = routes()
    with pytest.raises(PatchError):
        _ = root.merge_patch({"routes": {("10.0.0.0", 24): {"cost": 5}}})
    assert costs(root) == [0, 0]


def test_comma_in_a_single_key_is_not_composite() -> None:
    root = Root(rules=[Rule(name="a,b")])
    _ = root.merge_patch({"rules": {"a,b": {"priority": 1}}})
    assert root.rules[0].priority == 1


R1_TEXT = "10.0.0.0,24,192.168.1.1,GE3"
R2_TEXT = "10.1.0.0,16,192.168.1.1,"


def message_of(model: VcoModel, patch: dict[str, object]) -> str:
    with pytest.raises(PatchError) as exc:
        _ = model.merge_patch(patch)
    return exc.value.message


def test_a_composite_key_is_selected_by_its_parts_joined_with_commas() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {R1_TEXT: {"cost": 5}, R2_TEXT: {"cost": 6}}})
    assert costs(root) == [5, 6]
    assert select_items(routes(), "routes", R2_TEXT) == [1]

    root = routes()
    _ = root.merge_patch({"routes": {R2_TEXT: move(before=R1_TEXT)}})
    assert [route.dest for route in root.routes] == ["10.1.0.0", "10.0.0.0"]

    root = routes()
    _ = root.merge_patch({"routes": {R1_TEXT: delete()}})
    assert [route.dest for route in root.routes] == ["10.1.0.0"]


def test_an_add_under_the_comma_form_writes_the_parts_as_text() -> None:
    root = routes()
    _ = root.merge_patch({"routes": {"10.2.0.0,24,192.168.1.1,GE4": add({"cost": 1})}})
    new = root.routes[2]
    assert (new.dest, new.prefix, new.gw, new.intf) == (
        "10.2.0.0",
        "24",
        "192.168.1.1",
        "GE4",
    )

    # The quoted form and a tuple say the type themselves.
    quoted = '"10.2.0.0","24","192.168.1.1",GE4'
    for selector in (quoted, ("10.2.0.0", "24", "192.168.1.1", "GE4")):
        root = routes()
        _ = root.merge_patch({"routes": {selector: add()}})
        assert root.routes[2].prefix == "24"

    # So does the body.
    root = routes()
    _ = root.merge_patch(
        {"routes": {"10.2.0.0,24,192.168.1.1,GE4": add({"prefix": "24"})}}
    )
    assert root.routes[2].prefix == "24"
    assert "already in the list" in message_of(routes(), {"routes": {R1_TEXT: add()}})


def test_a_comma_key_part_selects_and_adds_as_its_text() -> None:
    assert select_items(routes(), "routes", "10.1.0.0,016,192.168.1.1,") == []
    assert select_items(routes(), "routes", "10.1.0.0,16,192.168.1.1,") == [1]
    huge = "1" + "0" * 4999
    assert select_items(routes(), "routes", f"10.1.0.0,{huge},192.168.1.1,") == []
    # Beside the stored 24, an add under 024 is another route, as the text says.
    root = routes()
    _ = root.merge_patch({"routes": {"10.0.0.0,024,192.168.1.1,GE3": add()}})
    assert [route.prefix for route in root.routes] == [24, 16, "024"]


STATIC_KEY_TEXT = "10.0.0.0,24,192.168.1.1,GE3"


def static_route(destination: str, prefix: object) -> dict[str, object]:
    return {
        "destination": destination,
        "netmask": "255.255.255.0",
        "cidrPrefix": prefix,
        "gateway": "192.168.1.1",
        "wanInterface": "GE3",
    }


def sent_static(held: SegmentRoutes) -> list[dict[str, Any]]:
    return held.model_dump(mode="json", by_alias=True)["static"]


@pytest.mark.parametrize(
    ("given", "held"),
    [(24, "24"), ("24", "24"), ("024", "024"), (0, "0")],
    ids=["int", "text", "text-with-a-leading-zero", "zero"],
)
def test_a_static_routes_prefix_is_held_as_text(given: object, held: str) -> None:
    route = StaticRoute.model_validate(static_route("10.0.0.0", given))
    assert route.cidr_prefix == held
    assert type(route.cidr_prefix) is str
    json_route = StaticRoute.model_validate_json(
        json.dumps(static_route("10.0.0.0", given))
    )
    assert json_route.cidr_prefix == held


@pytest.mark.parametrize("given", [True, False, 24.0, None])
def test_a_static_routes_prefix_that_is_no_text_or_whole_number_is_refused(
    given: object,
) -> None:
    with pytest.raises(ValidationError):
        _ = StaticRoute.model_validate(static_route("10.0.0.0", given))


def test_a_static_route_added_under_the_comma_form_sends_its_prefix_as_text() -> None:
    held = SegmentRoutes.model_validate({"static": [static_route("10.1.0.0", "16")]})
    _ = held.merge_patch(
        {"static": {"10.9.0.0,24,192.168.1.1,GE3": add({"netmask": "255.255.255.0"})}}
    )
    sent = sent_static(held)[1]
    assert (sent["destination"], sent["cidrPrefix"], sent["gateway"]) == (
        "10.9.0.0",
        "24",
        "192.168.1.1",
    )
    assert sent["wanInterface"] == "GE3"


@pytest.mark.parametrize(
    "key",
    [
        ("10.9.0.0", 24, "192.168.1.1", "GE3"),
        ("10.9.0.0", "24", "192.168.1.1", "GE3"),
        '"10.9.0.0",24,"192.168.1.1",GE3',
    ],
    ids=["tuple-with-a-number", "tuple-with-text", "quoted-with-a-number"],
)
def test_a_static_route_added_under_a_tuple_or_the_quoted_form_sends_its_prefix_as_text(
    key: object,
) -> None:
    held = SegmentRoutes.model_validate({"static": [static_route("10.1.0.0", "16")]})
    _ = held.merge_patch({"static": {key: add({"netmask": "255.255.255.0"})}})
    sent = sent_static(held)[1]
    expected = static_route("10.9.0.0", "24")
    assert {name: sent[name] for name in expected} == expected


def test_a_number_given_in_the_body_for_a_static_routes_prefix_is_stored_as_text() -> (
    None
):
    held = SegmentRoutes.model_validate({"static": [static_route("10.1.0.0", "16")]})
    _ = held.merge_patch(
        {
            "static": {
                "10.9.0.0,24,192.168.1.1,GE3": add(
                    {"netmask": "255.255.255.0", "cidr_prefix": 24}
                ),
                "10.1.0.0,16,192.168.1.1,GE3": {"cidr_prefix": 24},
            }
        }
    )
    assert [route.cidr_prefix for route in held.static] == ["24", "24"]
    assert [route["cidrPrefix"] for route in sent_static(held)] == ["24", "24"]


def test_an_add_under_the_comma_form_reads_a_number_part_as_its_field_does() -> None:
    def collector(host: str, port: int) -> dict[str, object]:
        return {"host": host, "protocol": "UDP", "port": port}

    held = SegmentSyslog.model_validate({"collectors": [collector("10.0.0.1", 514)]})
    _ = held.merge_patch(
        {
            "collectors": {
                "10.0.0.9,UDP,514": add(),
                "10.0.0.1,UDP,514": {"tag": "edited"},
            }
        }
    )
    added = held.collectors[1]
    assert (added.host, added.protocol, added.port) == ("10.0.0.9", "UDP", 514)
    assert type(added.port) is int
    assert held.collectors[0].tag == "edited"


STATIC_SELECTORS = [
    pytest.param(STATIC_KEY_TEXT, id="comma-form"),
    pytest.param(key_text(("10.0.0.0", "24", "192.168.1.1", "GE3")), id="key-text"),
    pytest.param(("10.0.0.0", "24", "192.168.1.1", "GE3"), id="tuple-with-text"),
    pytest.param(("10.0.0.0", 24, "192.168.1.1", "GE3"), id="tuple-with-a-number"),
]


def text_prefix_routes() -> SegmentRoutes:
    return SegmentRoutes.model_validate(
        {"static": [static_route("10.0.0.0", "24"), static_route("10.1.0.0", "16")]}
    )


@pytest.mark.parametrize("selector", STATIC_SELECTORS)
def test_every_key_form_edits_a_static_route_whose_prefix_is_text(
    selector: object,
) -> None:
    held = text_prefix_routes()
    _ = held.merge_patch({"static": {selector: {"cost": 5}}})
    assert [route["cost"] for route in sent_static(held)] == [5, 0]
    assert [route["cidrPrefix"] for route in sent_static(held)] == ["24", "16"]


@pytest.mark.parametrize("selector", STATIC_SELECTORS)
def test_every_key_form_deletes_a_static_route_whose_prefix_is_text(
    selector: object,
) -> None:
    held = text_prefix_routes()
    _ = held.merge_patch({"static": {selector: delete()}})
    assert [route["destination"] for route in sent_static(held)] == ["10.1.0.0"]


@pytest.mark.parametrize("selector", STATIC_SELECTORS)
def test_every_key_form_refuses_an_add_of_a_static_route_whose_prefix_is_text(
    selector: object,
) -> None:
    held = text_prefix_routes()
    message = message_of(held, {"static": {selector: add()}})
    assert "already in the list" in message
    assert len(held.static) == 2


def test_a_part_that_holds_a_comma_needs_the_quoted_form() -> None:
    root = Root(routes=[Route(dest="a,b", prefix=1, gw="g")])
    with pytest.raises(PatchError):
        _ = root.merge_patch({"routes": {"a,b,1,g,": {"cost": 1}}})
    _ = root.merge_patch({"routes": {'"a,b",1,g,""': {"cost": 1}}})
    assert costs(root) == [1]


def test_the_comma_split_is_tried_when_the_written_form_names_no_item() -> None:
    # Read as key_text writes it, "d,true,g,e" has the part True, not "true".
    root = Root(routes=[Route(dest="d", prefix="true", gw="g", intf="e")])
    _ = root.merge_patch({"routes": {"d,true,g,e": {"cost": 1}}})
    assert costs(root) == [1]


@pytest.mark.parametrize(
    ("ops", "starts"),
    [
        ({"10.0.0.0/24": {"cost": 5}}, "'10.0.0.0/24' is not a key of this list"),
        (
            {"10.9.0.0,24,192.168.1.1,GE3": {"cost": 5}},
            "no item has the key '10.9.0.0,24,192.168.1.1,GE3'",
        ),
        ({"10.0.0.0/24": add()}, "'10.0.0.0/24' is not a key of this list"),
    ],
)
def test_a_composite_key_refusal_shows_how_a_key_is_written(
    ops: dict[str, object], starts: str
) -> None:
    message = message_of(routes(), {"routes": ops})
    assert message.startswith(starts)
    assert (
        "its 4 parts (dest, prefix, gw, intf) joined by commas, as in " + R1_TEXT
    ) in message


def test_the_example_key_is_quoted_when_a_part_holds_a_comma() -> None:
    root = Root(routes=[Route(dest="a,b", prefix=1, gw="g")])
    assert 'as in "a,b",1,g,""' in message_of(root, {"routes": {"x": delete()}})
    assert "as in" not in message_of(Root(), {"routes": {"x": delete()}})


def test_select_by_label() -> None:
    root = links()
    _ = root.merge_patch({"links": {"MPLS": {"family": "IPv6"}}})
    assert [link.family for link in root.links] == ["IPv4", "IPv4", "IPv6"]


def test_shared_label_raises_and_lists_keys_and_positions() -> None:
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"Internet": {"family": "IPv6"}}})
    for shown in ("u1", "u2", "#0", "#1"):
        assert shown in str(exc.value)
    assert [link.family for link in root.links] == ["IPv4", "IPv4", "IPv4"]


def test_key_selects_among_a_shared_label() -> None:
    root = links()
    _ = root.merge_patch({"links": {("u2", "IPv4"): {"name": "Internet 2"}}})
    assert [link.name for link in root.links] == ["Internet", "Internet 2", "MPLS"]


def test_plus_adds_an_item_whose_label_is_taken() -> None:
    root = links()
    _ = root.merge_patch({"links": {"+": add({"name": "Internet"})}})
    assert len(root.links) == 4
    assert root.links[3].name == "Internet"
    assert root.links[3].internal_id is MISSING


def test_plus_with_a_key_already_there_raises() -> None:
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"+": add({"name": "x", "internal_id": "u1"})}})
    assert exc.value.path == Path.parse('links["+"]')
    assert "'u1,IPv4'" in exc.value.message
    assert "already in the list" in exc.value.message
    assert len(root.links) == 3


def test_two_adds_of_one_key_raise() -> None:
    root = routes()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch(
            {
                "routes": {
                    ("10.2.0.0", 24, "192.168.1.1", "GE4"): add(),
                    '"10.2.0.0",24,"192.168.1.1",GE4': add(),
                }
            }
        )
    assert "adds this key too" in exc.value.message
    assert len(root.routes) == 2

    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch(
            {
                "links": {
                    "+a": add({"name": "x", "internal_id": "u9"}),
                    "+b": add({"name": "y", "internal_id": "u9"}),
                }
            }
        )
    assert exc.value.path == Path.parse('links["+b"]')
    assert "adds this key too" in exc.value.message
    assert len(root.links) == 3


def test_a_composite_key_in_a_message_shows_its_parts_as_written() -> None:
    root = routes()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"routes": {R1_KEY: add()}})
    assert '\'"10.0.0.0",24,"192.168.1.1",GE3\' is already' in exc.value.message

    # The add's key comes into the list by a rename in the same patch.
    root = routes()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch(
            {
                "routes": {
                    R2_KEY: {"dest": "10.2.0.0"},
                    ("10.2.0.0", 16, "192.168.1.1", ""): add(),
                }
            }
        )
    assert '\'"10.2.0.0",16,"192.168.1.1",""\' is already' in exc.value.message


U1, U2, U3 = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)


def wan(*ids: uuid.UUID) -> WanData:
    return WanData.model_validate(
        {
            "links": [
                {"internalId": str(u), "name": "Internet", "type": "WIRED"} for u in ids
            ]
        }
    )


def test_non_scalar_key_part_selects_in_string_form() -> None:
    data = wan(U1)
    link = data_links(data)[0]
    _ = data.merge_patch(
        {"links": {(link.internal_id, link.addressing_version): {"name": "Renamed"}}}
    )
    assert [x.name for x in data_links(data)] == ["Renamed"]

    data = wan(U1)
    _ = data.merge_patch({"links": {(U1, "IPv4"): delete()}})
    assert data_links(data) == []


def test_non_scalar_key_part_names_an_anchor_and_an_added_key() -> None:
    data = wan(U1, U2)
    _ = data.merge_patch({"links": {(U2, "IPv4"): move(before=(U1, "IPv4"))}})
    assert [x.internal_id for x in data_links(data)] == [U2, U1]

    data = wan(U1)
    _ = data.merge_patch(
        {
            "links": {
                (U3, "IPv4"): add({"name": "New", "type": "WIRED"}, before=(U1, "IPv4"))
            }
        }
    )
    assert [x.internal_id for x in data_links(data)] == [U3, U1]


def test_add_body_may_give_the_selectors_key_under_another_name() -> None:
    data = wan(U1)
    body = {"internalId": str(U3), "name": "New", "type": "WIRED"}
    _ = data.merge_patch({"links": {(U3, "IPv4"): add(body)}})
    assert [x.internal_id for x in data_links(data)] == [U1, U3]


class Collector(VcoModel):
    logical_id: uuid.UUID
    host: str = ""


class Collectors(VcoModel):
    collectors: Annotated[list[Collector], KeyField("logical_id")] = Field(
        default_factory=list
    )


def test_one_part_uuid_key_names_an_anchor_given_as_a_uuid() -> None:
    model = Collectors(collectors=[Collector(logical_id=U1), Collector(logical_id=U2)])
    _ = model.merge_patch({"collectors": {str(U2): move(before=U1)}})
    assert [c.logical_id for c in model.collectors] == [U2, U1]


def data_links(data: WanData) -> list[WanLink]:
    assert data.links is not MISSING
    return data.links


def test_keyless_item_is_named_by_position_or_label() -> None:
    root = Root(links=[Link(name="old")])
    _ = root.merge_patch({"links": {at(0): {"family": "IPv6"}}})
    assert root.links[0].family == "IPv6"

    root = Root(links=[Link(name="old")])
    _ = root.merge_patch({"links": {"old": {"family": "IPv6"}}})
    assert root.links[0].family == "IPv6"


def test_add_under_a_name_where_the_key_is_composite_points_to_plus() -> None:
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"Backup": add()}})
    assert exc.value.path == Path.parse("links[Backup]")
    assert '"+"' in str(exc.value)
    assert len(root.links) == 3


def true_and_title_true() -> Root:
    """Two routes that differ in the one part ``true`` / ``True``.

    The text ``..,true,..`` is two keys.
    """
    return Root(
        routes=[
            Route(dest="a", prefix=24, gw="true", intf="GE3"),
            Route(dest="a", prefix=24, gw="True", intf="GE3"),
        ]
    )


AMBIGUOUS_TEXT = "a,24,true,GE3"


def test_a_text_two_readings_take_to_different_items_is_refused_not_guessed() -> None:
    root = true_and_title_true()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"routes": {AMBIGUOUS_TEXT: {"cost": 5}}})
    assert exc.value.path == Path(("routes", Key(("a", 24, True, "GE3"))))
    message = exc.value.message
    assert "ambiguous" in message
    assert "#0" in message
    assert "#1" in message
    assert "quoted form" in message
    assert costs(root) == [0, 0]


def test_the_quoted_form_a_refusal_gives_selects_the_item_it_names() -> None:
    for index in (0, 1):
        key = item_key(
            true_and_title_true().routes[index], ("dest", "prefix", "gw", "intf")
        )
        assert key is not None
        text = key_text(key)
        root = true_and_title_true()
        assert select_items(root, "routes", text) == [index]
        _ = root.merge_patch({"routes": {text: {"cost": 5}}})
        assert costs(root) == [5 if i == index else 0 for i in (0, 1)]
    with pytest.raises(PatchError, match="ambiguous") as exc:
        _ = true_and_title_true().merge_patch({"routes": {AMBIGUOUS_TEXT: {"cost": 5}}})
    for index in (0, 1):
        key = item_key(
            true_and_title_true().routes[index], ("dest", "prefix", "gw", "intf")
        )
        assert key is not None
        assert key_text(key) in exc.value.message


def test_an_anchor_two_readings_take_to_different_items_is_refused() -> None:
    root = true_and_title_true()
    root.routes.append(Route(dest="z", prefix=8, gw="g", intf="e"))
    with pytest.raises(PatchError, match="ambiguous"):
        _ = root.merge_patch({"routes": {"z,8,g,e": move(before=AMBIGUOUS_TEXT)}})


def test_the_example_of_a_composite_key_selects_its_item() -> None:
    root = true_and_title_true()
    message = message_of(root, {"routes": {"zz": {"cost": 1}}})
    example = message.split(", as in ")[1].split(";")[0]
    assert select_items(root, "routes", example) == [0]
    _ = root.merge_patch({"routes": {example: {"cost": 5}}})
    assert costs(root) == [5, 0]


def test_a_union_list_names_the_variant_of_each_item() -> None:
    routed = list_item_type(ModelInterfaces.model_fields["routed_interfaces"])
    assert routed is not None
    names = {
        "GE3": "RoutedInterfaceGe",
        "LAG1": "RoutedInterfaceLag",
        "CELL1": "RoutedInterfaceCell",
        "USB1": "RoutedInterfaceUsb",
    }
    for name, variant in names.items():
        assert routed.variant_of({"name": name}).__name__ == variant
    assert routed.variant_of({"name": "SFP1", "sfpType": "dsl"}).__name__ == (
        "RoutedInterfaceSfpDsl"
    )
    assert routed.variant_of("junk") is routed.variants[0]


def test_python_path_goes_through_a_union_item_to_a_field_of_its_variant() -> None:
    path = Path.parse(
        "models.edge510.routedInterfaces[LAG1].slaveInterfaces[GE5].portPriority"
    )
    assert str(python_path(path, DeviceSettingsData)) == (
        "models.edge510.routed_interfaces[LAG1].slave_interfaces[GE5].port_priority"
    )


def test_pair_items_never_pairs_an_added_item() -> None:
    node = node_child(Root, "rules")
    assert isinstance(node, KeyedList)
    old: list[object] = [{"name": "a", "ruleId": "la"}, {"name": "b"}]
    new: list[object] = [AddedItem({"name": "a"}), {"name": "b"}]

    pairing = pair_items(old, new, node)
    assert pairing is not None
    assert (pairing.pairs, pairing.added, pairing.removed) == (((1, 1),), (0,), (0,))

    plain = pair_items(old, [{"name": "a"}, {"name": "b"}], node)
    assert plain is not None
    assert plain.pairs == ((0, 0), (1, 1))
    # An item as read that cannot be told from another: the list is written whole.
    twins: list[object] = [{"name": "a"}, {"name": "a"}, {"name": "b"}]
    assert pair_items(twins, new, node) is None


def _added_rule() -> Rule:
    root = Root(rules=[])
    _ = root.merge_patch({"rules": {"z": add({"priority": 3})}})
    item = root.rules[0]
    assert item.is_added
    return item


def test_an_added_item_equals_the_same_item_however_built() -> None:
    item = _added_rule()
    assert item == Rule(name="z", priority=3)
    assert item == Rule.model_validate({"name": "z", "priority": 3})
    assert not Rule(name="z", priority=3).is_added


@pytest.mark.parametrize(
    "dump",
    [{}, {"mode": "json"}, {"by_alias": True}, {"exclude_unset": True}],
)
def test_an_added_item_dumps_as_an_item_not_added(dump: dict[str, Any]) -> None:
    item, plain = _added_rule(), Rule(name="z", priority=3)
    assert item.model_dump(**dump) == plain.model_dump(**dump)
    assert repr(item) == repr(plain)


@pytest.mark.parametrize(
    "copied",
    [
        copy.deepcopy,
        lambda item: item.model_copy(deep=True),
        lambda item: item.model_copy(),
        copy.copy,
        lambda item: pickle.loads(pickle.dumps(item)),
    ],
    ids=["deepcopy", "model_copy_deep", "model_copy", "copy", "pickle"],
)
def test_an_added_item_is_added_in_every_copy(copied: Any) -> None:
    item = _added_rule()
    copy_of_item: Rule = copied(item)
    assert copy_of_item is not item
    assert copy_of_item.is_added


def test_validate_keeps_the_added_item_and_construct_builds_one_not_added() -> None:
    item = _added_rule()
    assert Rule.model_validate(item) is item
    assert not Rule.model_construct(**item.model_dump()).is_added


def test_an_item_rebuilt_from_its_dump_is_not_added() -> None:
    item = _added_rule()
    assert not Rule.model_validate(item.model_dump()).is_added
    assert not Rule.model_validate_json(item.model_dump_json()).is_added
