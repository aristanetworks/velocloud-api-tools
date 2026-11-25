"""Three name-resolution behaviours on write, under the strict rules."""

import uuid
from collections.abc import Mapping
from typing import Annotated, ClassVar, cast

import pytest
from pydantic import MISSING, Field
from pyvelo.client.models.configuration import FirewallData
from pyvelo.config.resolver import ResolutionError, ResolverRegistry, resolve_name
from pyvelo.core import VcoModel, add, delete
from pyvelo.core.fields import KeyField, NameOf, Resolvable
from pyvelo.core.identity import (
    AddressGroupIdentity,
    LinkIdentity,
    SegmentIdentity,
    ServiceGroupIdentity,
)
from pyvelo.core.layers import Edge

from ._type_support import SegmentResolverFactory

AG = {
    "printers": AddressGroupIdentity(name="printers", id=7, logical_id=uuid.UUID(int=7))
}
SEG = {
    "Global Segment": SegmentIdentity(
        name="Global Segment", segment_id=0, object_id=1, logical_id=uuid.UUID(int=1)
    ),
    "Guest": SegmentIdentity(
        name="Guest", segment_id=2, object_id=3, logical_id=uuid.UUID(int=3)
    ),
}
LINK = {
    "wan1": LinkIdentity(
        name="wan1",
        id=1,
        logical_id="00:11:22",
        internal_id=uuid.UUID(int=9),
        edge_id=4,
    )
}


class Fake:
    tables: ClassVar[dict[type, Mapping[str, object]]] = {
        AddressGroupIdentity: AG,
        SegmentIdentity: SEG,
        ServiceGroupIdentity: {},
        LinkIdentity: LINK,
    }

    def resolve_sync[T](self, key: type[T], name: str, /) -> T:
        if key not in self.tables:
            raise LookupError(f"No resolver registered for {key.__name__}")
        return cast(T, resolve_name(name, dict(self.tables[key]), key.__name__)[1])

    def has_id_sync(
        self, identity_type: type[object], id_attr: str, value: object, /
    ) -> bool:
        return any(
            getattr(i, id_attr) == value
            for i in self.tables.get(identity_type, {}).values()
        )

    def resolver_for(self, identity_type: type[object], /) -> object | None:
        return self if identity_type in self.tables else None

    def identity_sync[T](
        self, identity_type: type[T], id_attr: str, value: object, /
    ) -> T:
        for identity in self.tables[identity_type].values():
            if getattr(identity, id_attr) == value:
                return cast(T, identity)
        raise ResolutionError(
            name=str(value), identity_type=identity_type, candidates=[]
        )


def fresh() -> FirewallData[Edge]:
    return (
        FirewallData[Edge]
        .model_validate(
            {
                "segments": [
                    {
                        "segment": {"segmentId": 0, "name": "Global Segment"},
                        "outbound": [],
                    }
                ]
            }
        )
        .for_edge()
    )


def add_rule(fw: FirewallData[Edge], match: dict[str, object]) -> None:
    _ = fw.merge_patch(
        {
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "r1": add({"action": {"allow_or_deny": "deny"}, "match": match})
                    }
                }
            }
        },
        resolvers=Fake(),
    )


def test_a_hostname_is_never_tried_as_an_address_group_name():
    fw = fresh()
    add_rule(fw, {"hostname": "printers"})
    m = fw.segments[0].outbound[0].match
    assert m.hostname == "printers"
    assert m.source_address_group is MISSING and m.destination_address_group is MISSING


def test_misspelled_segment_key_raises_with_a_suggestion():
    fw = fresh()
    with pytest.raises(ResolutionError) as exc:
        _ = fw.merge_patch({"segments": {"Gest": {"outbound": {}}}}, resolvers=Fake())
    assert exc.value.candidates == ["Guest"]
    assert [s.segment.name for s in fw.segments] == ["Global Segment"]


def test_segment_key_in_another_case_edits_the_existing_segment():
    fw = fresh()
    _ = fw.merge_patch(
        {
            "segments": {
                "global segment": {
                    "outbound": {
                        "r1": add({"action": {"allow_or_deny": "deny"}, "match": {}})
                    }
                }
            }
        },
        resolvers=Fake(),
    )
    assert [s.segment.name for s in fw.segments] == ["Global Segment"]
    assert [r.name for r in fw.segments[0].outbound] == ["r1"]


def test_segment_key_in_another_case_deletes_the_existing_segment():
    fw = fresh()
    _ = fw.merge_patch({"segments": {"GLOBAL SEGMENT": delete()}}, resolvers=Fake())
    assert fw.segments == []


def test_new_segment_by_a_key_in_another_case_gets_the_canonical_name():
    fw = fresh()
    _ = fw.merge_patch({"segments": {"guest": add({"outbound": {}})}}, resolvers=Fake())
    meta = fw.segments[1].segment
    assert (meta.name, meta.segment_id, meta.segment_logical_id) == (
        "Guest",
        2,
        uuid.UUID(int=3),
    )


def test_a_near_miss_segment_key_in_another_case_still_raises():
    fw = fresh()
    with pytest.raises(ResolutionError) as exc:
        _ = fw.merge_patch({"segments": {"gest": {"outbound": {}}}}, resolvers=Fake())
    assert exc.value.candidates == ["Guest"]
    assert [s.segment.name for s in fw.segments] == ["Global Segment"]


def test_a_key_the_marked_field_does_not_name_is_not_resolved():
    lan = Lan()
    _ = lan.merge_patch({"networks": {"corp": add()}}, resolvers=Fake())
    _ = lan.merge_patch({"networks": {"CORP": add()}}, resolvers=Fake())
    assert [n.name for n in lan.networks] == ["corp", "CORP"]


def test_segment_name_fills_its_ids():
    fw = fresh()
    _ = fw.merge_patch({"segments": {"Guest": add({"outbound": {}})}}, resolvers=Fake())
    meta = fw.segments[1].segment
    assert (meta.name, meta.segment_id, meta.segment_logical_id) == (
        "Guest",
        2,
        uuid.UUID(int=3),
    )


def test_literal_uuid_string_is_accepted_on_new_and_existing_rules():
    fw = fresh()
    add_rule(fw, {"sAddressGroup": str(uuid.UUID(int=7))})  # _pre_resolve path
    assert fw.segments[0].outbound[0].match.source_address_group == uuid.UUID(int=7)
    _ = fw.merge_patch(
        {
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "r1": {"match": {"dAddressGroup": str(uuid.UUID(int=8))}}
                    }
                }
            }
        },
        resolvers=Fake(),
    )  # _merge_field path
    assert fw.segments[0].outbound[0].match.destination_address_group == uuid.UUID(
        int=8
    )


def test_name_still_resolves_and_unknown_name_raises_on_both_paths():
    fw = fresh()
    add_rule(fw, {"sAddressGroup": "Printers"})
    assert fw.segments[0].outbound[0].match.source_address_group == uuid.UUID(int=7)
    with pytest.raises(ResolutionError):
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {"r1": {"match": {"sAddressGroup": "printerz"}}}
                    }
                }
            },
            resolvers=Fake(),
        )
    with pytest.raises(ResolutionError):
        add_rule(fresh(), {"sAddressGroup": "printerz"})


def test_enum_literal_on_a_resolvable_field():
    fw = fresh()
    _ = fw.merge_patch(
        {"segments": {"Global Segment": {"segment": {"type": "CDE"}}}},
        resolvers=Fake(),
    )
    assert fw.segments[0].segment.type_ == "CDE"


class Net(VcoModel):
    name: str
    segment_name: Annotated[str, NameOf(SegmentIdentity)] = ""
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0


class Lan(VcoModel):
    networks: Annotated[list[Net], KeyField("name")] = Field(default_factory=list)


def test_only_the_marked_field_fills_ids():
    lan = Lan()
    # "Guest" here is a network's name. It is not marked, so it is not tried as a segment.
    _ = lan.merge_patch({"networks": {"Guest": add()}}, resolvers=Fake())
    assert lan.networks[0].segment_id == 0
    _ = lan.merge_patch(
        {"networks": {"corp": add({"segment_name": "guest"})}}, resolvers=Fake()
    )
    assert lan.networks[1].segment_id == 2


class Uplink(VcoModel):
    name: str = ""
    logical_id: Annotated[str, Resolvable(LinkIdentity, "logical_id")] = ""


class Holder(VcoModel):
    uplink: Uplink = Uplink()


def test_string_ids_name_first_then_known_id_then_error():
    h = Holder()
    _ = h.merge_patch({"uplink": {"logical_id": "wan1"}}, resolvers=Fake())
    assert h.uplink.logical_id == "00:11:22"
    _ = h.merge_patch({"uplink": {"logical_id": "00:11:22"}}, resolvers=Fake())
    assert h.uplink.logical_id == "00:11:22"
    with pytest.raises(ResolutionError):
        _ = h.merge_patch({"uplink": {"logical_id": "ff:ff:ff"}}, resolvers=Fake())


class NoTables(Fake):
    tables: ClassVar[dict[type, Mapping[str, object]]] = {}


def test_missing_resolver_is_not_a_resolution_error():
    fw = fresh()
    with pytest.raises(LookupError) as exc:
        _ = fw.merge_patch(
            {"segments": {"NewSeg": add({"outbound": {}})}}, resolvers=NoTables()
        )
    assert not isinstance(exc.value, ResolutionError)


def test_a_new_segment_no_resolver_can_give_ids_to_is_refused_by_name():
    for resolvers in (NoTables(), None):
        fw = fresh()
        with pytest.raises(LookupError, match="'NewSeg'.*segment_id") as exc:
            _ = fw.merge_patch({"segments": {"NewSeg": add()}}, resolvers=resolvers)
        assert "segment_logical_id" in str(exc.value)
        assert [s.segment.name for s in fw.segments] == ["Global Segment"]


def test_the_refusal_of_a_name_with_no_ids_says_where_and_what_to_do():
    fw = fresh()
    with pytest.raises(LookupError) as exc:
        _ = fw.merge_patch({"segments": {"NewSeg": add()}})
    message = str(exc.value)
    assert "segments" in message
    assert "in the body" in message
    assert "resolver" in message


def test_a_new_segment_with_its_ids_given_needs_no_resolver():
    fw = fresh()
    segment = {
        "segmentId": 7,
        "type": "REGULAR",
        "segmentLogicalId": str(uuid.UUID(int=7)),
    }
    _ = fw.merge_patch(
        {"segments": {"NewSeg": add({"segment": segment, "outbound": {}})}},
        resolvers=NoTables(),
    )
    meta = fw.segments[1].segment
    assert (meta.name, meta.segment_id, meta.segment_logical_id) == (
        "NewSeg",
        7,
        uuid.UUID(int=7),
    )


def test_a_new_item_whose_key_is_not_a_name_needs_no_ids():
    lan = Lan()
    _ = lan.merge_patch({"networks": {"corp": add()}}, resolvers=NoTables())
    assert lan.networks[0].segment_id == 0


def test_resolution_error_is_one_class_on_both_import_paths():
    from pyvelo.core.identity import ResolutionError as CoreError

    assert CoreError is ResolutionError and issubclass(ResolutionError, KeyError)


async def test_registry_has_id_sync_reads_the_loaded_identities(
    make_segment_resolver: SegmentResolverFactory,
):
    resolver = await make_segment_resolver(
        [
            {
                "name": "Guest",
                "segment_id": 2,
                "object_id": 3,
                "segment_logical_id": uuid.UUID(int=3),
            }
        ]
    )
    registry = ResolverRegistry()
    registry.register(resolver)

    assert [i.name for i in resolver.identities()] == ["Guest"]
    assert registry.has_id_sync(SegmentIdentity, "segment_id", 2)
    assert registry.has_id_sync(SegmentIdentity, "logical_id", uuid.UUID(int=3))
    assert not registry.has_id_sync(SegmentIdentity, "segment_id", 9)
    # No resolver registered for the type: False, not an error.
    assert not registry.has_id_sync(AddressGroupIdentity, "id", 7)
