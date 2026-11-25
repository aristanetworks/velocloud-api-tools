"""A model a patch changes is checked as the parser would check it."""

import copy
import uuid
from collections.abc import Callable
from typing import Annotated, Any, Self, cast

import pytest
from pydantic import (
    MISSING,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    ValidatorFunctionWrapHandler,
    model_validator,
)
from pyvelo.client.models.configuration.device_settings.root import (
    DeviceSettingsData,
)
from pyvelo.core import KeyField, PatchError, Positional, VcoModel, add, at, delete

from .patch_models import Root, Rule
from .test_patch_strict import raises_at


class Flow(VcoModel):
    enabled: bool = False
    collectors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_a_collector(self) -> Self:
        if self.enabled and not self.collectors:
            raise ValueError("a collector is required when enabled")
        return self


class Wired(VcoModel):
    name: str
    speed: int = 100


class Wireless(VcoModel):
    name: str
    ssid: str


def _port_kind(value: object) -> str:
    name = (
        cast("dict[str, object]", value).get("name", "")
        if isinstance(value, dict)
        else getattr(value, "name", "")
    )
    return "wireless" if str(name).startswith("WLAN") else "wired"


Port = Annotated[
    Annotated[Wired, Tag("wired")] | Annotated[Wireless, Tag("wireless")],
    Discriminator(_port_kind),
]


class Creds(VcoModel):
    kind: str = "DHCP"
    user: str | MISSING = MISSING

    @model_validator(mode="after")
    def _only_pppoe_has_a_user(self) -> Self:
        if self.kind != "PPPOE":
            self.user = MISSING
        return self


class Site(VcoModel):
    flow: Flow = Field(default_factory=Flow)
    flows: Annotated[list[Flow], Positional()] = Field(default_factory=list)
    by_name: dict[str, Flow] = Field(default_factory=dict)
    ports: Annotated[list[Port], KeyField("name")] = Field(default_factory=list)
    uplink: Port | None = None
    creds: Creds = Field(default_factory=Creds)
    spares: list[Wired] | list[Wireless] = Field(default_factory=list)


def on() -> Flow:
    return Flow(enabled=True, collectors=["c"])


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"flow": {"collectors": {"c": delete()}}}, "flow"),
        ({"flow": {"collectors": []}}, "flow"),
        ({"flows": {at(0): {"collectors": []}}}, "flows[#0]"),
        ({"by_name": {"x": {"collectors": []}}}, "by_name[x]"),
        ({"by_name": {"y": add({"enabled": True})}}, "by_name[y]"),
    ],
)
def test_a_change_that_a_model_validator_refuses_raises_at_the_model(
    patch: dict[str, object], path: str
):
    site = Site(flow=on(), flows=[on()], by_name={"x": on()})
    dump = copy.deepcopy(site.model_dump())
    error = raises_at(site, patch, path)
    assert "a collector is required when enabled" in error.message
    assert isinstance(error.__cause__, ValidationError)
    assert site.model_dump() == dump


def snapshot(value: object) -> object:
    """What a model holds, recursively: its dump and the fields it was given."""
    if isinstance(value, VcoModel):
        return (
            value.model_dump(),
            set(value.model_fields_set),
            {name: snapshot(getattr(value, name)) for name in type(value).model_fields},
        )
    if isinstance(value, list):
        return [snapshot(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        return {k: snapshot(v) for k, v in cast("dict[object, object]", value).items()}
    return copy.deepcopy(value)


def two_rules() -> VcoModel:
    return Root(rules=[Rule(name="a", rule_id="i1"), Rule(name="b", rule_id="i2")])


def one_flow() -> VcoModel:
    return Site(flow=on(), by_name={"x": on()})


@pytest.mark.parametrize(
    ("make", "patch"),
    [
        (two_rules, {"sub": {"v": 9}, "count": True}),
        (two_rules, {"sub": {"v": 9}, "by_name": {"+": add({"v": 1})}}),
        (two_rules, {"sub": {"v": 9}, "rules": {"b": {"rule_id": "i1"}}}),
        (
            one_flow,
            {"by_name": {"x": {"enabled": True}}, "flow": {"collectors": []}},
        ),
    ],
    ids=["boolean", "add-under-plus", "repeated-alt-key", "validator"],
)
def test_a_refused_patch_leaves_the_model_alone(
    make: Callable[[], VcoModel], patch: dict[str, object]
):
    model = make()
    before = snapshot(model)
    with pytest.raises(PatchError):
        _ = model.merge_patch(patch)
    assert snapshot(model) == before


def test_a_model_validator_runs_on_the_model_merge_patch_is_called_on():
    flow = on()
    error = raises_at(flow, {"collectors": []}, "")
    assert "a collector is required when enabled" in error.message
    assert flow.collectors == ["c"]


def test_model_validators_see_the_whole_change_in_whatever_order_it_is_written():
    site = Site(flow=on())
    _ = site.merge_patch({"flow": {"collectors": [], "enabled": False}})
    assert site.flow == Flow()
    _ = site.merge_patch({"flow": {"enabled": True, "collectors": ["d"]}})
    assert site.flow == Flow(enabled=True, collectors=["d"])


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"ports": {"GE1": {"name": "WLAN1"}}}, "ports[GE1]"),
        ({"uplink": {"name": "WLAN1"}}, "uplink"),
    ],
)
def test_a_change_cannot_move_an_item_to_another_type_of_its_union(
    patch: dict[str, object], path: str
):
    site = Site(ports=[Wired(name="GE1")], uplink=Wired(name="GE1"))
    error = raises_at(site, patch, path)
    assert "Wired" in error.message
    assert site.ports[0].name == "GE1"
    assert site.uplink is not None and site.uplink.name == "GE1"
    _ = site.merge_patch({"ports": {"GE1": {"name": "GE2"}}})
    assert site.ports[0].name == "GE2"


def test_a_model_validator_that_rewrites_does_not_undo_the_patch():
    site = Site()
    _ = site.merge_patch({"creds": {"user": "u"}})
    assert site.creds.user == "u"


def test_an_item_of_the_second_list_type_of_a_field_can_be_changed():
    site = Site(spares=[Wireless(name="WLAN9", ssid="s")])
    _ = site.merge_patch({"spares": {at(0): {"ssid": "t"}}})
    assert site.spares == [Wireless(name="WLAN9", ssid="t")]


# --- the unions of the real models: one item of each type ---------------------

NSD_ROUTE: dict[str, Any] = {
    "cidrIp": "10.5.0.0",
    "cidrPrefix": 24,
    "netMask": "255.255.255.0",
    "destination": "10.5.0.0",
    "nsdLogicalId": str(uuid.UUID(int=5)),
}
UNIONS: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "routedInterfaces": [
        {"name": "GE3"},
        {"name": "SFP1"},
        {
            "name": "SFP2",
            "sfpType": "dsl",
            "dslSettings": {"mode": "vdsl2", "properties": {"profile": "17a"}},
        },
        {
            "name": "SFP3",
            "sfpType": "gpon",
            "gponSettings": {
                "subscriberLocationId": "x",
                "subscriberLocationMode": "ASCII",
            },
        },
        {"name": "LAG1"},
        {"name": "CELL1"},
        {"name": "USB1"},
    ],
    "segments": [
        {
            "segment": {"segmentId": 0, "name": "Global Segment"},
            "routes": {
                "static": [],
                "nsd": [
                    {**NSD_ROUTE, "name": "by-edge"},
                    {
                        **NSD_ROUTE,
                        "name": "by-gateway",
                        "nsdType": "dataCenter",
                        "nextHop": ["10.0.0.1"],
                    },
                ],
            },
        }
    ],
}


def unions() -> DeviceSettingsData:
    return DeviceSettingsData.model_validate(UNIONS).for_edge()


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("GE3", "RoutedInterfaceGe"),
        ("SFP1", "RoutedInterfaceSfpStandard"),
        ("SFP2", "RoutedInterfaceSfpDsl"),
        ("SFP3", "RoutedInterfaceSfpGpon"),
        ("LAG1", "RoutedInterfaceLag"),
        ("CELL1", "RoutedInterfaceCell"),
        ("USB1", "RoutedInterfaceUsb"),
    ],
)
def test_a_change_that_keeps_its_type_applies_to_each_routed_interface_type(
    name: str, kind: str
):
    data = unions()
    _ = data.merge_patch({"routed_interfaces": {name: {"disabled": True}}})
    assert data.routed_interfaces is not None
    (interface,) = [i for i in data.routed_interfaces if i.name == name]
    assert type(interface).__name__ == kind
    assert interface.disabled is True


@pytest.mark.parametrize(
    ("position", "kind"), [(0, "NsdEdgeRoute"), (1, "NsdVcgRoute")]
)
def test_a_change_that_keeps_its_type_applies_to_each_nsd_route_type(
    position: int, kind: str
):
    data = unions()
    patch = {"routes": {"nsd": {at(position): {"cost": 9}}}}
    _ = data.merge_patch({"segments": {"Global Segment": patch}})
    routes = data.segments[0].routes
    assert routes is not MISSING
    route = routes.nsd[position]
    assert type(route).__name__ == kind
    assert route.cost == 9


def test_a_nested_union_field_can_be_changed_and_cannot_change_type():
    data = unions()
    patch = {"SFP2": {"dsl_settings": {"properties": {"profile": "30a"}}}}
    _ = data.merge_patch({"routed_interfaces": patch})
    _ = raises_at(
        data,
        {"routed_interfaces": {"GE3": {"name": "LAG7"}}},
        "routed_interfaces[GE3]",
    )
    assert data.routed_interfaces is not None
    assert data.routed_interfaces[0].name == "GE3"


class Dicty(VcoModel):
    v: int = 0

    @model_validator(mode="wrap")
    @classmethod
    def _wants_a_dict(cls, data: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        return handler({**data, "v": data["v"]})


def test_a_validator_that_takes_its_input_to_be_a_dict_raises_a_patch_error():
    dicty = Dicty.model_validate({"v": 1})
    error = raises_at(dicty, {"v": 2}, "")
    assert "a validator of Dicty failed" in error.message
    assert "must accept a model instance" in error.message
    assert isinstance(error.__cause__, TypeError)
    assert dicty.v == 1
