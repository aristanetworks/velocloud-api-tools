"""A document in attribute names, and the same document in the VCO's keys.

``named_value`` writes each key a model declares by its attribute name, and
``vco_keys`` writes a document in attribute names back in the VCO's keys. The
two are tried on a model made here and then on every model class of pyvelo.
"""

import functools
import importlib
import itertools
import json
import pkgutil
from collections.abc import Iterator, Mapping
from types import UnionType
from typing import (
    Annotated,
    Any,
    NamedTuple,
    TypeAliasType,
    TypeVar,
    Union,  # pyright: ignore[reportDeprecated]  # the origin of ``T | MISSING``
    cast,
    get_args,
    get_origin,
)

import pytest
import pyvelo
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.device_settings.refs import DeviceSettingsRefs
from pyvelo.client.models.enterprise.service_data import AwsNvsBackupServer
from pyvelo.client.schemas.configuration import ConfigurationBase
from pyvelo.core import (
    KeyField,
    NamedDocumentError,
    Path,
    VcoModel,
    field_kind,
    named_path,
    named_value,
    vco_keys,
    wire_name,
)
from pyvelo.core.secrets import declares_secret


class Hop(VcoModel):
    next_hop: str = ""
    ping_response: bool = True


class Route(VcoModel):
    route_name: str = ""
    hops: Annotated[list[Hop], KeyField("next_hop")] = Field(default_factory=list)
    by_port: dict[str, Hop] = Field(default_factory=dict)
    rows: Hop | tuple[Hop, ...] | MISSING = MISSING
    free_form: dict[str, Any] = Field(default_factory=dict)


WIRE: dict[str, Any] = {
    "routeName": "r",
    "hops": [{"nextHop": "a", "pingResponse": False}],
    "byPort": {"next_hop": {"nextHop": "b", "pingResponse": True}},
    "rows": [{"nextHop": "c", "pingResponse": True}],
    "freeForm": {"next_hop": 1, "nextHop": 2},
    "serverAdded": {"next_hop": 3},
}
NAMED: dict[str, Any] = {
    "route_name": "r",
    "hops": [{"next_hop": "a", "ping_response": False}],
    "by_port": {"next_hop": {"next_hop": "b", "ping_response": True}},
    "rows": [{"next_hop": "c", "ping_response": True}],
    "free_form": {"next_hop": 1, "nextHop": 2},
    "serverAdded": {"next_hop": 3},
}


def test_a_tuple_of_rows_is_walked_as_a_list() -> None:
    assert named_value(WIRE, Route) == NAMED
    refs = {"deviceSettings:segment": [{"configurationId": 1, "moduleId": 2}]}
    assert named_value(refs, DeviceSettingsRefs) == {
        "segment": [{"configuration_id": 1, "module_id": 2}]
    }


def test_a_path_below_a_tuple_of_rows_is_named() -> None:
    assert named_path(Path.parse("rows[#0].nextHop"), Route) == "rows[#0].next_hop"
    held = Route(rows=(Hop(next_hop="c"),))
    assert named_path(Path.parse("rows[#0].nextHop"), held) == "rows[#0].next_hop"
    assert named_path(Path.parse("rows.nextHop"), Route) == "rows.next_hop"


def test_vco_keys_is_the_inverse_of_named_value() -> None:
    again = cast("dict[str, Any]", vco_keys(NAMED, Route))
    assert json.dumps(again) == json.dumps(WIRE)
    assert again["hops"] is not NAMED["hops"]


def test_vco_keys_shares_no_container_with_the_document() -> None:
    again = cast("dict[str, Any]", vco_keys(NAMED, Route))
    for key, name in (("freeForm", "free_form"), ("serverAdded", "serverAdded")):
        assert again[key] == NAMED[name]
        assert again[key] is not NAMED[name]
    assert again["hops"][0] is not NAMED["hops"][0]
    assert vco_keys(NAMED, None) == NAMED
    assert vco_keys(NAMED, None) is not NAMED


def test_vco_keys_at_a_path() -> None:
    assert vco_keys({"next_hop": "a"}, Route, Path(("hops", 0))) == {"nextHop": "a"}


def test_a_document_in_the_vcos_keys_is_refused() -> None:
    with pytest.raises(NamedDocumentError) as caught:
        _ = vco_keys(WIRE, Route)
    assert (caught.value.kind, caught.value.path) == ("vco_key", "route_name")
    assert "`routeName` is the VCO's key for `route_name`" in caught.value.message
    assert "model_validate" in caught.value.message
    assert str(caught.value) == f"route_name: {caught.value.message}"


def test_a_vco_key_beside_its_attribute_is_kept_as_named_value_keeps_both() -> None:
    stored = {"hops": [{"nextHop": "a"}, {"next_hop": "b", "nextHop": "c"}]}
    named = named_value(stored, Route)
    assert named == {"hops": [{"next_hop": "a"}, {"next_hop": "b", "nextHop": "c"}]}
    assert vco_keys(named, Route) == stored


def test_below_each_key_of_a_pair_the_attribute_names_are_converted() -> None:
    stored = {"byPort": {"ge1": {"nextHop": "a"}}, "by_port": {"ge2": {"nextHop": "b"}}}
    named = named_value(stored, Route)
    assert named == {
        "byPort": {"ge1": {"next_hop": "a"}},
        "by_port": {"ge2": {"next_hop": "b"}},
    }
    assert vco_keys(named, Route) == stored


def test_a_refusal_below_a_path_is_at_the_whole_path_in_attribute_names() -> None:
    with pytest.raises(NamedDocumentError) as caught:
        _ = vco_keys({"pingResponse": True}, Route, Path(("byPort", "ge1")))
    assert caught.value.path == "by_port.ge1.ping_response"
    with pytest.raises(NamedDocumentError) as caught:
        _ = vco_keys({"ge1": {"nextHop": "b"}}, Route, Path(("by_port",)))
    assert caught.value.path == "by_port.ge1.next_hop"
    with pytest.raises(NamedDocumentError) as caught:
        _ = vco_keys({"nextHop": "c"}, Route, Path(("rows",)))
    assert caught.value.path == "rows.next_hop"


def test_a_refusal_at_the_root_is_its_message_alone() -> None:
    error = NamedDocumentError("is not a document", Path(()), "invalid_value")
    assert str(error) == "is not a document"
    assert (error.message, error.path, error.kind) == (
        "is not a document",
        "",
        "invalid_value",
    )


def test_an_attribute_that_is_another_fields_alias_is_the_attribute() -> None:
    named = named_value({"addressFamily": "ipv4"}, AwsNvsBackupServer)
    assert named == {"address_family": "ipv4"}
    assert vco_keys(named, AwsNvsBackupServer) == {"addressFamily": "ipv4"}


type _Parts = tuple[str | int, ...]
"""The parts of a path from a field to a model class it holds."""

_UNION_ORIGINS = (Union, UnionType)  # pyright: ignore[reportDeprecated]  # still an origin


@functools.cache
def model_classes() -> tuple[type[VcoModel], ...]:
    """Every model class of pyvelo, and each generic one with each layer.

    The classes pyvelo defines are the subclasses of ``VcoModel`` in its
    modules, a class a test defines left out. A generic one is not taken as
    found with an argument: it is made here once per constraint of its type
    parameter, so the answer does not depend on which of them another test
    has made in this process.
    """
    for module in pkgutil.walk_packages(pyvelo.__path__, "pyvelo."):
        _ = importlib.import_module(module.name)
    found: dict[type[VcoModel], None] = {}
    seen: set[type[VcoModel]] = set()
    pending: list[type[VcoModel]] = [VcoModel]
    while pending:
        for cls in pending.pop().__subclasses__():
            if cls in seen or not cls.__module__.startswith("pyvelo."):
                continue
            seen.add(cls)
            pending.append(cls)
            if cls.__pydantic_generic_metadata__["origin"] is None:
                found[cls] = None
    for cls in list(found):
        parameters = cls.__pydantic_generic_metadata__["parameters"]
        constraints = [parameter.__constraints__ for parameter in parameters]
        if constraints and all(constraints):
            for args in itertools.product(*constraints):
                found[cls[args]] = None
    return tuple(found)


class Held(NamedTuple):
    """A model class a field can hold."""

    name: str
    """The attribute name of the field."""
    parts: _Parts
    """Where below the field's value the class is: ``0`` an item, ``"k"`` an entry."""
    cls: type[VcoModel]
    direct: bool
    """Whether the field's own type says so, one level deep.

    The class is the field's type, the type of the items of its list or of
    its tuple of any length, or of the values of its map, with no type alias
    on the way. It is not where it is behind a type alias, in a tuple of
    fixed length, or in a list or a map inside another.
    """


type _Holding = tuple[_Parts, type[VcoModel], bool]
"""A model class a value can hold: where below the value, the class, if direct."""


def _holdings(
    annotation: object,
    parts: _Parts,
    given: Mapping[object, object],
    inside: tuple[TypeAliasType, ...],
    *,
    direct: bool,
) -> Iterator[_Holding]:
    """Each model class a value of *annotation*, found at *parts*, can hold.

    A type alias is read as what it stands for, with its arguments in the
    place of its parameters (*given*), and not again inside itself (*inside*).
    """
    if isinstance(annotation, TypeVar):
        annotation = given.get(annotation, annotation)
    origin, args = get_origin(annotation), get_args(annotation)
    alias = origin if isinstance(origin, TypeAliasType) else annotation
    if isinstance(alias, TypeAliasType):
        if not any(alias is other for other in inside):
            named = [given.get(arg, arg) for arg in args]
            below: dict[object, object] = dict(
                zip(alias.__type_params__, named, strict=False)
            )
            value: object = alias.__value__
            yield from _holdings(value, parts, below, (*inside, alias), direct=False)
    elif isinstance(annotation, type) and issubclass(annotation, VcoModel):
        yield parts, _with_arguments(annotation, given), direct
    else:
        for inner, place, one_level in _members(annotation, parts):
            still = direct and one_level
            yield from _holdings(inner, place, given, inside, direct=still)


def _members(annotation: object, parts: _Parts) -> list[tuple[object, _Parts, bool]]:
    """The types *annotation*, found at *parts*, is made of, and where their values are.

    An item of a list, and of a tuple of any length, is at ``0``; an item of a
    fixed tuple at its position; a value of a map at ``"k"``. The last of
    each says whether the walk reads that far: not into a fixed tuple, and
    not into a list or a map that is inside another.
    """
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return [(args[0], parts, True)]
    if origin in _UNION_ORIGINS:
        return [(member, parts, True) for member in args]
    if origin is list or (origin is tuple and args[1:] == (Ellipsis,)):
        return [(args[0], (*parts, 0), not parts)]
    if origin is dict:
        return [(args[1], (*parts, "k"), not parts)]
    if origin is tuple:
        return [(member, (*parts, at), False) for at, member in enumerate(args)]
    return []


def _with_arguments(
    cls: type[VcoModel], given: Mapping[object, object]
) -> type[VcoModel]:
    """*cls* with each type parameter it was written with replaced by its argument.

    A generic model written inside a type alias with the alias's parameter
    (``Module[L]``) is a class of its own; the model the alias stands for is
    the same generic one with the argument the alias was given.
    """
    metadata = cls.__pydantic_generic_metadata__
    origin, args = metadata["origin"], metadata["args"]
    if origin is None or not any(arg in given for arg in args):
        return cls
    replaced = origin[tuple(given.get(arg, arg) for arg in args)]
    return cast("type[VcoModel]", replaced)


def held_by(cls: type[VcoModel]) -> list[Held]:
    """Each model class a field of *cls* can hold, once per place below the field.

    The field's annotation is resolved here, not by the walk under test. A
    class found at one place two ways is direct if either way is.
    """
    found: dict[tuple[str, _Parts, type[VcoModel]], bool] = {}
    for name, field_info in cls.model_fields.items():
        held_type = field_kind(field_info).inner_type
        for parts, held, direct in _holdings(held_type, (), {}, (), direct=True):
            place = (name, parts, held)
            found[place] = found.get(place, False) or direct
    return [Held(*place, direct) for place, direct in found.items()]


def declared_places() -> Iterator[tuple[type[VcoModel], Path, type[VcoModel]]]:
    """Each place a model class is declared at: an owner, a path of it, the class.

    A class is at its own root, and wherever a field of a class holds it: at
    the field, in an item of its list or in an entry of its map.
    """
    for cls in model_classes():
        yield cls, Path(()), cls
        for held in held_by(cls):
            yield cls, Path((wire_name(cls, held.name), *held.parts)), held.cls


def test_every_attribute_name_goes_back_to_its_key_wherever_it_is_declared() -> None:
    checked = 0
    for owner, path, held in declared_places():
        for name in held.model_fields:
            key = wire_name(held, name)
            named = named_value({key: 0}, owner, path)
            assert vco_keys(named, owner, path) == {key: 0}, (owner, path, name)
            checked += 1
    assert checked > 6000


def test_no_two_attributes_of_a_place_are_written_as_one_key() -> None:
    # ``vco_keys`` would write both values under the one key, and keep the last.
    places: dict[tuple[type[VcoModel], Path], dict[str, str]] = {}
    for owner, path, held in declared_places():
        written = places.setdefault((owner, path), {})
        for name in held.model_fields:
            key = wire_name(held, name)
            assert written.setdefault(key, name) == name, (owner, path, key)


def unread_fields() -> set[str]:
    """Each field that can hold a model class the walk does not read below it.

    A field is ``Class.field``, the class by its generic origin. The walk
    reads a class a field holds directly (``Held.direct``), and no other:
    that this is what ``named_value`` does is the test after the next one,
    wherever a class has a key to show it.
    """
    unread: set[str] = set()
    for cls in model_classes():
        origin = cls.__pydantic_generic_metadata__["origin"] or cls
        for held in held_by(cls):
            if not held.direct:
                unread.add(f"{origin.__name__}.{held.name}")
    return unread


UNREAD = {
    "ConfigurationBase.modules",  # read by the configuration's own override
    "EdgeConfiguration.modules",  # the same field, inherited
    "EnterpriseConfigurationProfile.modules",  # the same field, inherited
    "GetEdgeResult.ha",
    "GetEdgeSdwanPeers.sort_by",
    "EdgeFiltersAnd.and_",
    "EdgeFiltersOr.or_",
    "GetEnterpriseEdgeList.filters",
    "GetEnterpriseEdgeListItem.ha",
    "GetEnterpriseEdges.filters",
    "GetEnterpriseEdgesItem.ha",
    "LinkQualitySampleMetadata.state_map",
    "EdgeLinkSeriesItem.series",
    "EdgeNetworkInterfaceSeriesItem.series",
    "GetLinkDegradationDetails.filters",
    "GetLinkDegradationSummary.filters",
    "GetLinkFlapSummary.filters",
    "GetLinkInsightsOverview.filters",
    "_LinkInsightsRequest.filters",
    "ApiFiltersAnd.and_",
    "ApiFiltersOr.or_",
}


def test_the_fields_the_walk_does_not_read_are_the_recorded_set() -> None:
    assert unread_fields() == UNREAD


def test_only_the_rows_of_a_configuration_hold_a_secret_the_walk_does_not_read() -> (
    None
):
    """No ``named_dump`` shows a secret the models mark.

    The walk masks a secret only below a field it reads. The rows of a
    configuration are below one it does not, and a configuration dumps them
    itself, each by its own class. So no other class holds a secret below a
    field of that sort, and none holds a configuration: its dump would walk
    the configuration as any model, and show the secrets of its rows.
    """
    for cls in model_classes():
        for held in held_by(cls):
            assert not issubclass(held.cls, ConfigurationBase), (cls, held.name)
            if not held.direct and not issubclass(cls, ConfigurationBase):
                assert not declares_secret(held.cls), (cls, held.name)


def _nested(parts: _Parts, value: object) -> object:
    """*value* at *parts* of a JSON value: an item of a list, an entry of a map."""
    for part in reversed(parts):
        value = [value] * (part + 1) if isinstance(part, int) else {part: value}
    return value


def _found(value: object, parts: _Parts) -> object:
    """What *value* holds at *parts*, each the key or the position it is."""
    for part in parts:
        value = cast("Any", value)[part]
    return value


def test_a_key_is_named_below_a_field_exactly_where_the_field_is_read() -> None:
    checked = 0
    for cls in model_classes():
        for held in held_by(cls):
            keys = [wire_name(held.cls, name) for name in held.cls.model_fields]
            renamed = [key for key in keys if key not in held.cls.model_fields]
            if not renamed:
                # Every key of the class is its attribute name: a named
                # document is the VCO's there, read or not.
                continue
            document = _nested(
                (wire_name(cls, held.name), *held.parts), {renamed[0]: 0}
            )
            named = _found(named_value(document, cls), (held.name, *held.parts))
            kept = named == {renamed[0]: 0}
            assert kept is not held.direct, (cls, held)
            checked += 1
    assert checked > 500
