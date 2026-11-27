"""A mapping given as a module's data is sent as it is, so its keys are the VCO's.

``plan_module`` takes a module's data as a model or as a mapping. A model is
dumped by the VCO's keys. A mapping is the JSON to send: a key in it that is a
field's attribute name, where the VCO has another key for the field, would
reach the VCO as a key it does not know, and the field itself would be left out
or left as it was. A dump of a configuration model has such keys unless it is
made with ``by_alias=True``, so a plan refuses each one the mapping adds to the
data as read.
"""

import copy
from typing import Annotated, Any, Final

import pytest
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    FirewallConfigurationModule,
    GenericConfigurationModule,
)
from pyvelo.core import Alias, ConfigContext, Edge, VcoModel
from pyvelo.core.names import attribute_name_keys
from pyvelo.core.patch import PatchError
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json
from segment_helpers import GUEST, firewall_entry

from .write_helpers import fw


def raw(**extra: object) -> dict[str, Any]:
    """Firewall data as read: one segment with a rule, and one inbound rule.

    Every key the models write is there, so that a dump of the data read from
    it, by the VCO's keys, is this JSON again.
    """
    rule = {
        "name": "a",
        "match": {"sip": "10.0.0.1", "dport_low": 80},
        "action": {"type": "port_forwarding"},
        "ruleLogicalId": "x",
        "loggingEnabled": True,
    }
    seed = {
        "segments": [firewall_entry(GUEST)],
        "inbound": [rule],
        "inboundV6": [],
        "firewall_enabled": True,
    }
    whole = (
        FirewallData[Edge].model_validate(seed).model_dump(mode="json", by_alias=True)
    )
    return {**whole, **extra}


RULE: Final[dict[str, Any]] = raw()["inbound"][0]
"""The inbound rule of ``raw()``, as the VCO returns it."""


def module(**extra: object) -> FirewallConfigurationModule[Edge]:
    read = fw(raw(**extra), layer=Edge)
    assert isinstance(read.data, FirewallData)
    return read


def by_name(read: FirewallConfigurationModule[Edge]) -> dict[str, Any]:
    return read.data.model_dump(mode="json")


def by_the_vcos_keys(read: FirewallConfigurationModule[Edge]) -> dict[str, Any]:
    return read.data.model_dump(mode="json", by_alias=True)


def refused(
    read: FirewallConfigurationModule[Edge], data: dict[str, Any]
) -> PatchError:
    with pytest.raises(PatchError) as raised:
        _ = plan_module(read, data)
    assert raised.value.kind == "unknown_field"
    return raised.value


def test_an_unedited_dump_by_name_is_refused_and_by_the_vcos_keys_plans_nothing() -> (
    None
):
    read = module()
    assert plan_module(read, by_the_vcos_keys(read)).operation == "none"
    error = refused(read, by_name(read))
    assert error.path == Path.parse("inbound[#0].match.ip_version")
    assert "it has 42 keys, each the python name of a field" in error.message


def test_a_rule_renamed_in_a_dump_by_name_is_refused_and_by_the_vcos_keys_is_sent() -> (
    None
):
    read = module()
    wire = by_the_vcos_keys(read)
    wire["inbound"][0]["name"] = "b"
    plan = plan_module(read, wire)
    assert [str(change.path) for change in plan.changes] == ["inbound"]
    assert plan.data["inbound"] == [{**RULE, "name": "b"}]
    assert {"ruleLogicalId", "loggingEnabled"} <= set(RULE)
    assert {"sip", "dport_low"} <= set(RULE["match"])
    assert "inboundV6" in plan.data

    named = by_name(read)
    named["inbound"][0]["name"] = "b"
    error = refused(read, named)
    assert error.path == Path.parse("inbound[#0].match.ip_version")


def test_a_key_the_models_do_not_know_is_planned_as_before() -> None:
    read = module()
    data = {**by_the_vcos_keys(read), "x-extra": {"rule_logical_id": 1}}
    plan = plan_module(read, data)
    assert [str(change.path) for change in plan.changes] == ['"x-extra"']
    assert plan.data["x-extra"] == {"rule_logical_id": 1}


def test_a_key_that_is_the_attribute_name_and_the_vcos_key_is_planned_as_before() -> (
    None
):
    read = module()
    plan = plan_module(read, {**by_the_vcos_keys(read), "firewall_enabled": False})
    assert [str(change.path) for change in plan.changes] == ["firewall_enabled"]
    assert plan.data["firewall_enabled"] is False


def test_a_stored_key_that_equals_an_attribute_name_is_sent_back_as_it_was() -> None:
    stored_rule = {**RULE, "name": "b", "ruleLogicalId": "y", "logging_enabled": False}
    read = fw(
        {**raw(inbound=[RULE, stored_rule]), "inbound_v6": "kept"},
        layer=Edge,
    )
    data = copy.deepcopy(read.raw_data)
    assert isinstance(data, dict)
    assert plan_module(read, data).operation == "none"
    # The rule that holds the key may move, and another change may be made.
    data["inbound"].reverse()
    data["firewall_enabled"] = False
    plan = plan_module(read, data)
    assert plan.data["inbound"][0]["logging_enabled"] is False
    assert plan.data["inbound_v6"] == "kept"


def test_a_stored_key_does_not_let_the_same_name_in_at_another_field() -> None:
    stored_rule = {**RULE, "logging_enabled": False}
    read = fw(raw(inbound=[stored_rule]), layer=Edge)
    data = copy.deepcopy(read.raw_data)
    assert isinstance(data, dict)
    rule = data["segments"][0]["outbound"][0]
    rule["logging_enabled"] = rule.pop("loggingEnabled", True)
    error = refused(read, data)
    assert error.path == Path.parse("segments[#0].outbound[#0].logging_enabled")


def test_the_refusal_names_a_key_inside_a_rule_of_a_segment_and_says_what_to_do() -> (
    None
):
    read = module()
    data = by_the_vcos_keys(read)
    rule = data["segments"][0]["outbound"][0]
    rule["rule_logical_id"] = rule.pop("ruleLogicalId")
    error = refused(read, data)
    assert error.path == Path.parse("segments[#0].outbound[#0].rule_logical_id")
    assert error.message == (
        "firewall: the data given is a mapping, which is sent to the VCO as it "
        "is, and it has a key that is the python name of a field whose key in "
        "the VCO's JSON is another: segments[#0].outbound[#0].rule_logical_id "
        "(the VCO's key is ruleLogicalId); an item is named by its position in "
        "the list given. The VCO would be sent a key it does not know, and the "
        "field itself would be left out, or left as it was. Give the model "
        "itself (module.data), or its dump by the VCO's keys "
        '(model_dump(mode="json", by_alias=True)), or make the change with a '
        "patch (merge_patch on the model, or apply through the pipeline), which "
        "takes a field by either name"
    )
    assert str(error).startswith(
        "segments[#0].outbound[#0].rule_logical_id: firewall: the data given"
    )


def test_the_refusal_lists_a_few_keys_and_counts_the_rest() -> None:
    read = module()
    error = refused(read, by_name(read))
    assert error.message.startswith(
        "firewall: the data given is a mapping, which is sent to the VCO as it "
        "is, and it has 42 keys, each the python name of a field whose key in "
        "the VCO's JSON is another: inbound[#0].match.ip_version (the VCO's key "
        "is ipVersion), inbound[#0].match.source_ip (the VCO's key is sip), "
        "inbound[#0].match.source_subnet_mask and 39 more; an item is named by "
        "its position in the list given. The VCO would be sent keys it does not "
        "know, and the fields themselves would be left out, or left as they "
        "were. Give the model itself (module.data), or "
    )


def test_a_key_of_the_data_itself_is_named_without_a_position() -> None:
    read = module()
    data = by_the_vcos_keys(read)
    data["inbound_v6"] = data.pop("inboundV6")
    error = refused(read, data)
    assert error.path == Path.parse("inbound_v6")
    assert (
        "it has a key that is the python name of a field whose key in the VCO's "
        "JSON is another: inbound_v6 (the VCO's key is inboundV6). The VCO would"
    ) in error.message


def test_a_module_with_no_data_as_read_is_refused_every_such_key() -> None:
    data = FirewallData[Edge].model_validate(raw())
    unread = FirewallConfigurationModule[Edge].model_construct(
        name="firewall", data=data
    )
    assert unread.raw_data is MISSING
    plan = plan_module(
        unread,
        data.model_dump(mode="json", by_alias=True),
        level=ConfigContext.EDGE,
    )
    assert plan.operation == "update"
    assert plan.data["inbound"] == [RULE]
    with pytest.raises(PatchError) as raised:
        _ = plan_module(unread, data.model_dump(mode="json"), level=ConfigContext.EDGE)
    assert raised.value.kind == "unknown_field"
    assert raised.value.path == Path.parse("inbound[#0].match.ip_version")
    assert "it has 42 keys, each the python name" in raised.value.message


def test_data_that_is_no_model_is_planned_as_before() -> None:
    read = GenericConfigurationModule.model_validate(
        make_module_json("analyticsSettings", {"analytics_mode": "a"})
    )
    plan = plan_module(read, {"analytics_mode": "b", "rule_logical_id": 1})
    assert sorted(str(change.path) for change in plan.changes) == [
        "analytics_mode",
        "rule_logical_id",
    ]


class Port(VcoModel):
    port_low: int = 0


class Named(VcoModel):
    """Its ``port_low`` is the VCO's key, where ``Port`` has ``portLow``."""

    low: Annotated[int, Alias("port_low")] = 0
    other_name: str = ""


class Holder(VcoModel):
    by_name: dict[str, Port] = Field(default_factory=dict)
    in_order: list[Port] = Field(default_factory=list)
    one_or_more: Port | list[Port] | None = None
    of_two: Port | Named | None = None
    free_form: dict[str, Any] = Field(default_factory=dict)


def found(value: object, read: object = MISSING) -> list[tuple[str, str]]:
    keys = attribute_name_keys(value, Holder, read)
    return [(str(path), wire) for path, wire in keys]


def test_the_keys_are_found_in_a_map_a_list_and_a_field_of_either_shape() -> None:
    document = {
        "byName": {"port_low": {"port_low": 1, "portLow": 2}},
        "inOrder": [{"portLow": 1}, {"port_low": 2}],
        "oneOrMore": [{"port_low": 3}],
    }
    assert found(document) == [
        ("by_name.port_low.port_low", "portLow"),
        ("in_order[#1].port_low", "portLow"),
        ("one_or_more[#0].port_low", "portLow"),
    ]
    assert found({"oneOrMore": {"port_low": 3}}) == [
        ("one_or_more.port_low", "portLow")
    ]
    assert found({"one_or_more": None}) == [("one_or_more", "oneOrMore")]


def test_a_key_one_class_of_a_union_writes_is_no_attribute_name_alone() -> None:
    assert found({"ofTwo": {"port_low": 1}}) == []
    assert found({"ofTwo": {"other_name": "x"}}) == [("of_two.other_name", "otherName")]


def test_nothing_is_found_where_no_class_says_what_is_there() -> None:
    assert found({"freeForm": {"port_low": 1, "in_order": [{"port_low": 1}]}}) == []
    assert found({"unknown": {"in_order": [{"port_low": 1}]}}) == []
    assert attribute_name_keys({"in_order": []}, None) == []


def test_a_key_as_read_at_a_field_is_left_out_for_every_item_of_its_list() -> None:
    read = {"inOrder": [{"port_low": 1}], "byName": {"a": {"portLow": 1}}}
    document = {
        "inOrder": [{"portLow": 9}, {"port_low": 1}, {"port_low": 2}],
        "byName": {"a": {"port_low": 1}},
    }
    assert found(document, read) == [("by_name.a.port_low", "portLow")]
