"""A configuration model dumps by attribute name, and by the VCO's keys on request.

``model_dump()`` of a model a configuration caller reads writes each field under
its attribute name; ``model_dump(by_alias=True)`` writes the keys the VCO stores.
The models that do so are those of ``pyvelo.client.models.configuration`` and
``pyvelo.client.models.enterprise``, the module rows, the enterprise service,
object group and segment rows, the configurations that hold the module rows, and
every model a field of one of them can hold, so that one dump never mixes the two
forms below such a model.
"""

import importlib
import json
import pkgutil
import typing
import uuid

import httpx
import pytest
import pyvelo.client.models
import pyvelo.client.schemas
from hub_state import rule_uses_hub
from pydantic import BaseModel
from pyvelo.client.enums import NetworkSegmentType
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.enterprise.network_segment import NetworkSegmentData
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    ConfigurationModuleBase,
)
from pyvelo.client.schemas.edge import CompositeFirewallModule
from pyvelo.client.schemas.enterprise import (
    EnterpriseServiceBase,
    InsertEnterpriseNetworkSegment,
    NetworkSegment,
    ObjectGroupBase,
)
from pyvelo.config.diff import Modified, diff_intents
from pyvelo.core import ConfigContext, ConfigModel, Edge, Profile, VcoModel
from pyvelo.core.patch import PatchError
from pyvelo.testing import network_segment_wire
from segment_helpers import GLOBAL, GUEST, firewall_entry, qos_entry

from ._type_support import (
    JsonRpcResponseFactory,
    MockVcoClientFactory,
    parse_json_object,
)

_MODEL_PACKAGES = (
    "pyvelo.client.models.configuration",
    "pyvelo.client.models.enterprise",
)
"""Every model defined below one of these dumps by attribute name."""

_ROWS: tuple[type[VcoModel], ...] = (
    ConfigurationModuleBase,
    ConfigurationBase,
    EnterpriseServiceBase,
    ObjectGroupBase,
    NetworkSegment,
)
"""The rows that dump by attribute name, each with its subclasses."""


def _import_models() -> None:
    """Import every module that defines a model, so that each class is found."""
    for package in (pyvelo.client.models, pyvelo.client.schemas):
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            _ = importlib.import_module(module.name)


def _with_subclasses(cls: type[VcoModel]) -> set[type[VcoModel]]:
    """*cls* and its subclasses to the bottom, its parametrized forms among them."""
    found: set[type[VcoModel]] = {cls}
    todo = [cls]
    while todo:
        for sub in todo.pop().__subclasses__():
            if sub not in found:
                found.add(sub)
                todo.append(sub)
    return found


def _models_in(annotation: object) -> set[type[BaseModel]]:
    """Every model class written in *annotation*.

    Read through ``Annotated``, unions, containers, type aliases and the
    arguments of a generic model.
    """
    found: set[type[BaseModel]] = set()
    seen: set[int] = set()

    def walk(tp: object) -> None:
        if id(tp) in seen:
            return
        seen.add(id(tp))
        if isinstance(tp, typing.TypeAliasType):
            walk(tp.__value__)
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            found.add(tp)
            for arg in tp.__pydantic_generic_metadata__["args"]:
                walk(arg)
            return
        origin: object = typing.get_origin(tp)
        if isinstance(origin, typing.TypeAliasType):
            walk(origin.__value__)
        for arg in typing.get_args(tp):
            walk(arg)

    walk(annotation)
    return found


def _held(cls: type[VcoModel]) -> list[tuple[str, type[VcoModel]]]:
    """Each ``VcoModel`` class a field of *cls* can hold, with the field's name."""
    return [
        (name, model)
        for name, info in cls.model_fields.items()
        for model in sorted(_models_in(info.annotation), key=_name)
        if issubclass(model, VcoModel)
    ]


def _name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def open_boundary() -> list[str]:
    """``Class.field -> Other`` for each plain ``VcoModel`` a ``ConfigModel`` holds.

    A model that dumps by attribute name and holds one that dumps by alias would
    mix the two forms in one dump.
    """
    _import_models()
    return sorted(
        {
            f"{cls.__name__}.{name} -> {held.__name__}"
            for cls in _with_subclasses(ConfigModel)
            for name, held in _held(cls)
            if not issubclass(held, ConfigModel)
        }
    )


def _named_scope() -> set[type[VcoModel]]:
    """The classes that are meant to dump by attribute name.

    They are found without asking what a class derives from: the classes
    defined in the model packages, the rows with their subclasses, and every
    model a field of one of those can hold, to any depth.
    """
    _import_models()
    scope = {
        cls
        for cls in _with_subclasses(VcoModel)
        if cls.__module__.startswith(_MODEL_PACKAGES)
    }
    for row in _ROWS:
        scope |= _with_subclasses(row)
    todo = list(scope)
    while todo:
        for _, held in _held(todo.pop()):
            for cls in _with_subclasses(held) - scope:
                scope.add(cls)
                todo.append(cls)
    return scope


async def test_a_module_dumps_by_attribute_name() -> None:
    vpn = (await rule_uses_hub()).profile.device_settings.segments[1].vpn
    assert "backhaul_edges" in vpn.model_dump()
    assert "backHaulEdges" not in vpn.model_dump()
    assert "backHaulEdges" in vpn.model_dump(by_alias=True)
    assert "backhaul_edges" not in vpn.model_dump(by_alias=True)


def test_the_boundary_is_closed() -> None:
    """Every model reachable from a field of a ``ConfigModel`` is a ``ConfigModel``."""
    assert open_boundary() == []


def test_every_model_meant_to_dump_by_name_does() -> None:
    """Read from each class's own config: a base in the wrong order would not show."""
    scope = _named_scope()
    assert len(scope) > 390  # the classes, and the parametrized forms of the generic
    by_alias = sorted(
        _name(cls)
        for cls in scope
        if cls.model_config.get("serialize_by_alias") is not False
    )
    assert by_alias == []
    assert sorted(_name(cls) for cls in scope if not issubclass(cls, ConfigModel)) == []
    assert all(
        cls.model_config.get("serialize_by_alias") is False
        for cls in _with_subclasses(ConfigModel)
    )


async def test_a_configuration_dumps_by_attribute_name_all_the_way_down() -> None:
    """What ``state.target_config`` gives: the row, its modules and their data."""
    config = (await rule_uses_hub()).target_config
    named = config.model_dump()
    assert "configuration_type" in named
    settings = named["modules"][0]
    assert "configuration_id" in settings
    assert "backhaul_edges" in settings["data"]["segments"][1]["vpn"]
    # The refs of the device settings are fields of a model. Those of QoS are
    # held as the JSON read, which no dump renames.
    assert "logical_id" in settings["refs"]["vpn_edge_hub"]
    assert "logicalId" in named["modules"][1]["refs"]["deviceSettings:backHaulEdge"]
    wire = config.model_dump(by_alias=True)
    assert "configurationType" in wire
    assert "configurationId" in wire["modules"][0]
    assert "backHaulEdges" in wire["modules"][0]["data"]["segments"][1]["vpn"]
    assert "logicalId" in wire["modules"][0]["refs"]["deviceSettings:vpn:edgeHub"]
    assert "logicalId" in wire["modules"][1]["refs"]["deviceSettings:backHaulEdge"]


async def test_a_dump_leaves_out_the_same_fields_under_either_name() -> None:
    """A dump by default names is the dump ``by_alias=False`` gives.

    An edge layer leaves out the fields of the profile layer and the fields it
    stores nothing for, and a profile's device settings the fields absent at
    that level: each is left out of a dump whatever its keys are.
    """
    firewall = FirewallData[Edge].model_validate(
        {"segments": [firewall_entry(GLOBAL)]},
        context={"config_level": ConfigContext.EDGE},
    )
    assert firewall.model_dump() == firewall.model_dump(by_alias=False)
    assert set(firewall.model_dump()) == {"inbound", "inbound_v6", "segments"}
    assert set(firewall.model_dump(by_alias=True)) == {
        "inbound",
        "inboundV6",
        "segments",
    }

    settings = (await rule_uses_hub()).profile.device_settings
    assert settings.model_dump() == settings.model_dump(by_alias=False)
    assert "routed_interfaces" not in settings.model_dump()
    assert "routedInterfaces" not in settings.model_dump(by_alias=True)
    assert len(settings.model_dump()) == len(settings.model_dump(by_alias=True))


async def test_a_compact_dump_names_fields_as_it_did() -> None:
    """Attribute names unless ``by_alias=True`` is passed."""
    vpn = (await rule_uses_hub()).profile.device_settings.segments[1].vpn
    assert set(vpn.model_dump(context={"compact": True})) == {
        "backhaul_edges",
        "enabled",
        "hub_edges",
    }
    assert set(vpn.model_dump(context={"compact": True}, by_alias=True)) == {
        "backHaulEdges",
        "edgeToEdgeHub",
        "enabled",
    }


def test_a_fixed_list_refusal_names_the_field_by_its_attribute() -> None:
    """As ``FixedListError`` names it (``rule_logical_id differs``)."""
    data = QosData[Profile].model_validate(
        {"segments": [qos_entry(GUEST)]},
        context={"config_level": ConfigContext.PROFILE},
    )
    patch = {"defaults": {"Default-Internet-UDP": {"ruleLogicalId": str(uuid.uuid4())}}}
    with pytest.raises(PatchError) as raised:
        data.merge_patch({"segments": {"Guest": patch}})
    assert raised.value.kind == "fixed_list"
    assert "this patch changes rule_logical_id of" in raised.value.message
    assert "ruleLogicalId" not in raised.value.message.split(" To edit ")[0]


def test_a_diff_of_two_models_names_the_field_that_differs_by_its_attribute() -> None:
    rule = {"name": "r", "action": {"allow_or_deny": "deny"}, "match": {}}
    was = FirewallOutboundRule.model_validate({**rule, "ruleLogicalId": "a"})
    now = FirewallOutboundRule.model_validate({**rule, "ruleLogicalId": "b"})
    (change,) = diff_intents([was], [now], "name").changes
    assert isinstance(change, Modified)
    assert [field.path for field in change.changes] == ["rule_logical_id"]


def test_a_result_row_holds_a_configuration_model_and_keeps_its_own_keys() -> None:
    """A result row is a wire dump; ``by_alias`` gives one form throughout."""
    row = CompositeFirewallModule.model_validate(
        {
            "version": "1",
            "schemaVersion": "3.0.0",
            "type": "ENTERPRISE",
            "data": {"segments": [firewall_entry(GLOBAL)]},
        }
    )
    assert isinstance(row.data, ConfigModel)
    assert not isinstance(row, ConfigModel)
    mixed = row.model_dump()
    assert "schemaVersion" in mixed
    assert "network_protection_settings" in mixed["data"]
    wire = row.model_dump(by_alias=True)
    assert "schemaVersion" in wire
    assert "networkProtectionSettings" in wire["data"]
    named = row.model_dump(by_alias=False)
    assert "schema_version" in named
    assert "network_protection_settings" in named["data"]


async def test_a_request_that_holds_a_configuration_model_is_sent_in_the_vcos_keys(
    mock_vco_client: MockVcoClientFactory,
    json_rpc_response: JsonRpcResponseFactory,
) -> None:
    sent: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(parse_json_object(request.content)["params"])
        stored = parse_json_object(json.dumps(network_segment_wire("Guest", 1)))
        return httpx.Response(200, json=json_rpc_response(result=stored))

    client = await mock_vco_client(handler)
    data = NetworkSegmentData(
        segment_id=1, delegate_to_enterprise=True, delegate_to_enterprise_proxy=False
    )
    assert "segment_id" in data.model_dump()
    request = InsertEnterpriseNetworkSegment(
        name="Guest", type_=NetworkSegmentType.REGULAR, data=data
    )
    _ = await client.insert_enterprise_network_segment(request)
    assert sent == [
        {
            "name": "Guest",
            "type": "REGULAR",
            "data": {
                "segmentId": 1,
                "delegateToEnterprise": True,
                "delegateToEnterpriseProxy": False,
                "serviceVlan": None,
            },
        }
    ]
