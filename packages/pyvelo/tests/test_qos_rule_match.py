"""A QoS rule match is written with the address fields of its IP version only.

The Orchestrator refuses a rule that has the fields of the other family
(FW00000023), as it does for a firewall rule; ``QosRuleMatch`` drops them the way
``OutboundRuleMatch`` does. A rule as read keeps the keys it had.
"""

import copy
from typing import Any

import pytest
from pyvelo.client.models.configuration.qos.rule import QosRuleMatch
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import ConfigContext, Edge
from pyvelo.testing import make_module_json

EDGE = ConfigContext.EDGE

V4_KEYS = {"sip", "dip", "sip_prenat_enabled", "dip_prenat_enabled"}
V6_KEYS = {"sipV6", "dipV6", "sip_prenat_enabledV6", "dip_prenat_enabledV6"}
ADDRESS_KEYS = V4_KEYS | V6_KEYS


def written(match: QosRuleMatch) -> dict[str, Any]:
    return match.model_dump(mode="json", by_alias=True)


def address_keys(match: QosRuleMatch) -> set[str]:
    return ADDRESS_KEYS & set(written(match))


def test_an_ipv4_match_has_no_ipv6_fields() -> None:
    match = QosRuleMatch.model_validate({"ipVersion": "IPv4"})
    assert address_keys(match) == V4_KEYS
    assert (written(match)["sip"], written(match)["sip_prenat_enabled"]) == ("any", 0)


def test_an_ipv6_match_has_no_ipv4_fields() -> None:
    match = QosRuleMatch.model_validate({"ipVersion": "IPv6"})
    assert address_keys(match) == V6_KEYS
    assert (written(match)["sipV6"], written(match)["sip_prenat_enabledV6"]) == (
        "any",
        0,
    )


def test_an_ipv4v6_match_has_both_families() -> None:
    assert address_keys(QosRuleMatch.model_validate({"ipVersion": "IPv4v6"})) == (
        ADDRESS_KEYS
    )
    assert address_keys(QosRuleMatch.model_validate({})) == ADDRESS_KEYS


def test_the_fields_of_the_family_it_has_are_read_as_given() -> None:
    match = QosRuleMatch.model_validate(
        {"ipVersion": "IPv4", "sip": "10.0.0.1", "sip_prenat_enabled": 1}
    )
    assert (match.source_ip, match.source_ip_pre_nat_enabled) == ("10.0.0.1", 1)
    other = QosRuleMatch.model_validate({"ipVersion": "IPv6", "dipV6": "2001:db8::1"})
    assert other.destination_ip_v6 == "2001:db8::1"


def test_the_config_level_of_the_validation_context_is_kept() -> None:
    match = QosRuleMatch.model_validate(
        {"ipVersion": "IPv4"}, context={"config_level": EDGE}
    )
    assert match.config_context is EDGE
    assert address_keys(match) == V4_KEYS


# --- through a module write --------------------------------------------------


def rule(name: str, match: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "match": match,
        "action": {
            "edge2EdgeRouteAction": {},
            "edge2DataCenterRouteAction": {},
            "edge2CloudRouteAction": {},
            "QoS": {},
        },
        "ruleLogicalId": "00000000-0000-0000-0000-0000000000a1",
    }


def qos(*rules: dict[str, Any]) -> QosConfigurationModule[Edge]:
    segment = {
        "segmentId": 0,
        "name": "Global Segment",
        "type": "REGULAR",
        "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
    }
    raw = {"segments": [{"segment": segment, "rules": list(rules)}]}
    return QosConfigurationModule[Edge].model_validate(
        make_module_json("QOS", copy.deepcopy(raw))
    )


IPV4_AS_READ = {
    "ipVersion": "IPv4",
    "sip": "any",
    "dip": "any",
    "sip_prenat_enabled": 0,
    "dip_prenat_enabled": 0,
    "ssm": "255.255.255.255",
    "dsm": "255.255.255.255",
}
IPV4_WITH_IPV6_KEYS = {
    **IPV4_AS_READ,
    "sipV6": "any",
    "dipV6": "any",
    "sip_prenat_enabledV6": 0,
    "dip_prenat_enabledV6": 0,
}
IPV6_AS_READ = {"ipVersion": "IPv6", "sipV6": "any", "dipV6": "any"}
IPV4V6_AS_READ = {
    "ipVersion": "IPv4v6",
    "sip": "any",
    "dip": "any",
    "sipV6": "any",
    "dipV6": "any",
    "ssm": "255.255.255.255",
    "dsm": "255.255.255.255",
}
MATCHES = [IPV4_AS_READ, IPV4_WITH_IPV6_KEYS, IPV6_AS_READ, IPV4V6_AS_READ, {}]


@pytest.mark.parametrize("match", MATCHES, ids=lambda m: str(sorted(m)))
def test_a_rule_as_read_is_written_back_unchanged(match: dict[str, Any]) -> None:
    raw_rule = rule("r1", match)
    plan = plan_module(qos(raw_rule), level=EDGE)
    assert (plan.operation, plan.changes) == ("none", ())
    assert plan.data["segments"][0]["rules"] == [raw_rule]


@pytest.mark.parametrize("match", MATCHES, ids=lambda m: str(sorted(m)))
def test_an_edit_next_to_a_match_leaves_its_keys_as_they_were(
    match: dict[str, Any],
) -> None:
    raw_rule = rule("r1", match)
    mod = qos(raw_rule)
    mod.data.segments[0].rules[0].match.hostname = "example.invalid"
    plan = plan_module(mod, level=EDGE)
    assert [str(c.path) for c in plan.changes] == [
        'segments["Global Segment"].rules[r1].match.hostname'
    ]
    written_match = plan.data["segments"][0]["rules"][0]["match"]
    assert written_match == {**match, "hostname": "example.invalid"}


def test_a_new_rule_copied_from_an_ipv4_rule_has_no_ipv6_fields() -> None:
    mod = qos(rule("r1", IPV4_AS_READ))
    rules = mod.data.segments[0].rules
    added = rules[0].model_copy(deep=True)
    added.name = "r2"
    rules.append(added)
    plan = plan_module(mod, level=EDGE)
    assert plan.operation == "update"
    assert [str(c.path) for c in plan.changes] == [
        'segments["Global Segment"].rules[r2]'
    ]
    new_match = plan.data["segments"][0]["rules"][1]["match"]
    assert ADDRESS_KEYS & set(new_match) == V4_KEYS
    assert new_match["ipVersion"] == "IPv4"


def test_a_new_rule_built_for_ipv6_has_no_ipv4_fields() -> None:
    mod = qos(rule("r1", IPV4_AS_READ))
    rules = mod.data.segments[0].rules
    rules.append(
        type(rules[0]).model_validate(rule("r2", {"ipVersion": "IPv6"})),
    )
    plan = plan_module(mod, level=EDGE)
    new_match = plan.data["segments"][0]["rules"][1]["match"]
    assert ADDRESS_KEYS & set(new_match) == V6_KEYS


def test_a_new_rule_for_both_families_has_every_address_field() -> None:
    mod = qos(rule("r1", IPV4_AS_READ))
    rules = mod.data.segments[0].rules
    rules.append(type(rules[0]).model_validate(rule("r2", {})))
    plan = plan_module(mod, level=EDGE)
    new_match = plan.data["segments"][0]["rules"][1]["match"]
    assert ADDRESS_KEYS & set(new_match) == ADDRESS_KEYS
