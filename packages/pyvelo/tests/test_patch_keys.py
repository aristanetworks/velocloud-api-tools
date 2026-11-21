"""Keys in a patch (spec 5.10.3 "Keys", "Duplicate keys"): alt keys, composite keys, labels and ``KeySpec``."""

import uuid
from typing import Annotated

import pytest
from pydantic import MISSING, Field
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
from pyvelo.core.fields import get_key_spec
from pyvelo.core.rebase import Path

from .patch_models import Link, Root, Route, Rule


def two() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="b", rule_id="i2")])


def two_named_a() -> Root:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="a", rule_id="i2")])


def names(root: Root) -> list[str]:
    return [rule.name for rule in root.rules]


def test_select_by_alt_key():
    root = two()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]

    root = two()
    _ = root.merge_patch({"rules": {"i1": delete()}})
    assert names(root) == ["b"]

    root = two()
    _ = root.merge_patch({"rules": {"a": move(after="i2")}})
    assert names(root) == ["b", "a"]


def test_key_wins_over_another_items_alt_key():
    root = Root(rules=[Rule(name="a", rule_id="b"), Rule(name="b", rule_id="i2")])
    _ = root.merge_patch({"rules": {"b": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_add_takes_the_key_not_the_alt_key():
    root = two()
    _ = root.merge_patch({"rules": {"i9": add()}})
    assert names(root) == ["a", "b", "i9"]
    assert root.rules[2].rule_id is MISSING


def test_duplicate_names_select_by_alt_key():
    root = two_named_a()
    _ = root.merge_patch({"rules": {"i2": {"priority": 5}}})
    assert [r.priority for r in root.rules] == [0, 5]


def test_duplicate_names_raise_and_list_alt_keys():
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": {"priority": 5}}})
    assert exc.value.path == Path.parse("rules[a]")
    assert "i1" in str(exc.value)
    assert "i2" in str(exc.value)
    assert [r.priority for r in root.rules] == [0, 0]


def test_add_of_a_duplicated_key_raises():
    root = two_named_a()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"a": add()}})
    assert exc.value.path == Path.parse("rules[a]")
    assert len(root.rules) == 2


def test_keyfield_needs_a_path():
    with pytest.raises(TypeError):
        _ = KeyField()


class Keyed(VcoModel):
    one: Annotated[list[Rule], KeyField("a")] = Field(default_factory=list)
    two: Annotated[list[Rule], KeyField("a", "b")] = Field(default_factory=list)
    alt: Annotated[list[Rule], KeyField("a", alt="i", label="n")] = Field(
        default_factory=list
    )
    none: list[Rule] = Field(default_factory=list)


def test_key_spec():
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


def test_add_under_another_items_alt_key_raises():
    root = Root(rules=[Rule(name="a", rule_id="i1")])
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"rules": {"i1": add()}})
    assert exc.value.path == Path.parse("rules[i1]")
    assert names(root) == ["a"]


def test_key_and_alt_selectors_for_one_item_raise():
    root = Root(rules=[Rule(name="a", rule_id="i1")])
    with pytest.raises(PatchError):
        _ = root.merge_patch({"rules": {"a": {"priority": 1}, "i1": delete()}})
    assert names(root) == ["a"]
    assert root.rules[0].priority == 0


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


def test_select_by_tuple():
    root = routes()
    _ = root.merge_patch({"routes": {R1_KEY: {"cost": 5}}})
    assert costs(root) == [5, 0]


def test_select_by_string_form():
    root = routes()
    _ = root.merge_patch({"routes": {'"10.0.0.0",24,"192.168.1.1",GE3': {"cost": 5}}})
    assert costs(root) == [5, 0]


def test_parts_compare_as_strings():
    root = routes()
    _ = root.merge_patch(
        {"routes": {("10.0.0.0", "24", "192.168.1.1", "GE3"): {"cost": 5}}}
    )
    assert costs(root) == [5, 0]


def test_empty_string_is_a_key_part():
    root = routes()
    _ = root.merge_patch({"routes": {R2_KEY: {"cost": 5}}})
    assert costs(root) == [0, 5]

    root = routes()
    _ = root.merge_patch({"routes": {'"10.1.0.0",16,"192.168.1.1",""': {"cost": 5}}})
    assert costs(root) == [0, 5]


def test_add_by_composite_key_writes_the_parts():
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


def test_delete_and_anchor_by_composite_key():
    root = routes()
    _ = root.merge_patch({"routes": {R1_KEY: delete()}})
    assert [route.dest for route in root.routes] == ["10.1.0.0"]

    root = routes()
    _ = root.merge_patch({"routes": {R2_KEY: move(before=R1_KEY)}})
    assert [route.dest for route in root.routes] == ["10.1.0.0", "10.0.0.0"]


def test_wrong_number_of_parts_names_no_item():
    root = routes()
    with pytest.raises(PatchError):
        _ = root.merge_patch({"routes": {("10.0.0.0", 24): {"cost": 5}}})
    assert costs(root) == [0, 0]


def test_comma_in_a_single_key_is_not_composite():
    root = Root(rules=[Rule(name="a,b")])
    _ = root.merge_patch({"rules": {"a,b": {"priority": 1}}})
    assert root.rules[0].priority == 1


def test_select_by_label():
    root = links()
    _ = root.merge_patch({"links": {"MPLS": {"family": "IPv6"}}})
    assert [link.family for link in root.links] == ["IPv4", "IPv4", "IPv6"]


def test_shared_label_raises_and_lists_keys_and_positions():
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"Internet": {"family": "IPv6"}}})
    for shown in ("u1", "u2", "#0", "#1"):
        assert shown in str(exc.value)
    assert [link.family for link in root.links] == ["IPv4", "IPv4", "IPv4"]


def test_key_selects_among_a_shared_label():
    root = links()
    _ = root.merge_patch({"links": {("u2", "IPv4"): {"name": "Internet 2"}}})
    assert [link.name for link in root.links] == ["Internet", "Internet 2", "MPLS"]


def test_plus_adds_an_item_whose_label_is_taken():
    root = links()
    _ = root.merge_patch({"links": {"+": add({"name": "Internet"})}})
    assert len(root.links) == 4
    assert root.links[3].name == "Internet"
    assert root.links[3].internal_id is MISSING


def test_plus_with_a_key_already_there_raises():
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"+": add({"name": "x", "internal_id": "u1"})}})
    assert exc.value.path == Path.parse('links["+"]')
    assert "'u1,IPv4'" in exc.value.message
    assert "already in the list" in exc.value.message
    assert len(root.links) == 3


def test_two_adds_of_one_key_raise():
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


def test_a_composite_key_in_a_message_shows_its_parts_as_written():
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


def test_non_scalar_key_part_selects_in_string_form():
    data = wan(U1)
    link = data_links(data)[0]
    _ = data.merge_patch(
        {"links": {(link.internal_id, link.addressing_version): {"name": "Renamed"}}}
    )
    assert [x.name for x in data_links(data)] == ["Renamed"]

    data = wan(U1)
    _ = data.merge_patch({"links": {(U1, "IPv4"): delete()}})
    assert data_links(data) == []


def test_non_scalar_key_part_names_an_anchor_and_an_added_key():
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


def data_links(data: WanData) -> list[WanLink]:
    assert data.links is not MISSING
    return data.links


def test_keyless_item_is_named_by_position_or_label():
    root = Root(links=[Link(name="old")])
    _ = root.merge_patch({"links": {at(0): {"family": "IPv6"}}})
    assert root.links[0].family == "IPv6"

    root = Root(links=[Link(name="old")])
    _ = root.merge_patch({"links": {"old": {"family": "IPv6"}}})
    assert root.links[0].family == "IPv6"


def test_add_under_a_name_where_the_key_is_composite_points_to_plus():
    root = links()
    with pytest.raises(PatchError) as exc:
        _ = root.merge_patch({"links": {"Backup": add()}})
    assert exc.value.path == Path.parse("links[Backup]")
    assert '"+"' in str(exc.value)
    assert len(root.links) == 3
