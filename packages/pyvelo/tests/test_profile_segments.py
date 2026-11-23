"""The factory entries of a new profile segment ship as package data, with builders; the test helpers built on them work."""

import copy
import hashlib
import uuid
from importlib.resources import files
from typing import Any

import pytest
import segment_helpers
from pydantic import MISSING
from pyvelo.client.models.configuration.profile_segments import (
    DEFAULT_NAMES,
    default_rules,
    descriptor_of,
    new_segment_entry,
    segment_template,
)
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.core.identity import SegmentIdentity
from segment_helpers import GLOBAL, GUEST, VOICE

TEMPLATE_SHA256 = "06b5a1a8199fee223ce217c45fcbfb65eb0868bbb62a93fb95116bb60a053b19"
MODULES = ("deviceSettings", "QOS", "firewall")


def without_rule_ids(value: Any) -> Any:
    """*value* with every ``ruleLogicalId`` key removed, at any depth."""
    if isinstance(value, dict):
        return {
            k: without_rule_ids(v) for k, v in value.items() if k != "ruleLogicalId"
        }
    if isinstance(value, list):
        return [without_rule_ids(v) for v in value]
    return value


def keys_of(value: Any) -> set[str]:
    """Every dict key found in *value*, at any depth."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def rule_ids(entry: dict[str, Any]) -> list[str]:
    return [r["ruleLogicalId"] for r in [*entry["rules"], *entry["defaults"]]]


def test_the_packaged_template_is_the_companion_file():
    data = (
        files("pyvelo.client.models.configuration")
        .joinpath("new_segment_templates.json")
        .read_bytes()
    )
    assert hashlib.sha256(data).hexdigest() == TEMPLATE_SHA256


def test_the_template_has_no_rule_ids_and_no_descriptor():
    for name in MODULES:
        template = segment_template(name)
        assert "ruleLogicalId" not in keys_of(template)
        assert "segment" not in template
    assert "ruleLogicalId" not in keys_of(default_rules())


@pytest.mark.parametrize("name", MODULES)
def test_each_module_entry_is_the_template_with_the_descriptor(name: str):
    entry = new_segment_entry(name, segment_helpers.identity(GUEST))
    assert without_rule_ids(entry) == {
        "segment": segment_helpers.descriptor(GUEST),
        **segment_template(name),
    }
    assert entry["segment"] == descriptor_of(segment_helpers.identity(GUEST))


def test_the_qos_entry_has_the_rules_and_the_four_defaults():
    entry = new_segment_entry("QOS", segment_helpers.identity(GUEST))
    assert len(entry["rules"]) == 22
    assert tuple(d["name"] for d in entry["defaults"]) == DEFAULT_NAMES
    assert tuple(d["name"] for d in default_rules()) == DEFAULT_NAMES


def test_the_firewall_entry_has_one_allow_any_rule_and_no_rule_id():
    entry = new_segment_entry("firewall", segment_helpers.identity(GUEST))
    assert [r["name"] for r in entry["outbound"]] == ["AllowAny"]
    assert "ruleLogicalId" not in keys_of(entry)


def test_every_qos_rule_gets_a_new_id():
    first = rule_ids(new_segment_entry("QOS", segment_helpers.identity(GUEST)))
    second = rule_ids(new_segment_entry("QOS", segment_helpers.identity(GUEST)))
    assert len(first) == 26
    assert len(set(first)) == 26
    assert all(str(uuid.UUID(i)) == i for i in first)
    assert len(second) == 26
    assert not set(first) & set(second)


def test_qos_rules_replace_the_template_rules():
    rule = {"name": "Mine", "match": {"appid": 1}, "ruleLogicalId": str(uuid.uuid4())}
    given = copy.deepcopy(rule)
    entry = new_segment_entry("QOS", segment_helpers.identity(GUEST), qos_rules=[given])
    assert entry["rules"] == [rule]
    assert entry["rules"][0] is not given
    assert tuple(d["name"] for d in entry["defaults"]) == DEFAULT_NAMES
    assert all(d["ruleLogicalId"] for d in entry["defaults"])
    emptied = new_segment_entry("QOS", segment_helpers.identity(GUEST), qos_rules=[])
    assert emptied["rules"] == []


def test_calls_return_copies():
    entry = new_segment_entry("QOS", segment_helpers.identity(GUEST))
    entry["rules"].clear()
    entry["defaults"][0]["name"] = "changed"
    template = segment_template("QOS")
    template["rules"].clear()
    rules = default_rules()
    rules[0]["name"] = "changed"
    assert len(segment_template("QOS")["rules"]) == 22
    assert tuple(d["name"] for d in default_rules()) == DEFAULT_NAMES
    assert tuple(d["name"] for d in segment_template("QOS")["defaults"]) == (
        DEFAULT_NAMES
    )


def test_an_unknown_module_raises():
    with pytest.raises(ValueError) as error:
        segment_template("WAN")
    for name in MODULES:
        assert name in str(error.value)


# The helpers the later tasks build their tests on.


def test_the_descriptor_and_the_identity_describe_one_segment():
    assert segment_helpers.identity(VOICE) == SegmentIdentity(
        name="Voice",
        segment_id=4,
        object_id=504,
        logical_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
    )
    assert segment_helpers.descriptor(VOICE) == descriptor_of(
        segment_helpers.identity(VOICE)
    )


def test_the_helper_entries_are_the_factory_entries():
    qos = segment_helpers.qos_entry(GUEST)
    assert qos["segment"] == segment_helpers.descriptor(GUEST)
    assert qos["rules"] == []
    assert tuple(d["name"] for d in qos["defaults"]) == DEFAULT_NAMES
    assert all(d["ruleLogicalId"] for d in qos["defaults"])
    assert "defaults" not in segment_helpers.qos_entry(GUEST, defaults=MISSING)
    assert segment_helpers.qos_entry(GUEST, defaults=None)["defaults"] is None
    rule = {"name": "Mine", "ruleLogicalId": str(uuid.uuid4())}
    assert segment_helpers.qos_entry(GUEST, rules=[rule])["rules"] == [rule]
    firewall = segment_helpers.firewall_entry(GUEST)
    assert [r["name"] for r in firewall["outbound"]] == ["AllowAny"]
    assert firewall["outbound"][0]["ruleLogicalId"]
    ds = segment_helpers.ds_entry(GUEST)
    assert ds["segment"] == segment_helpers.descriptor(GUEST)
    assert ds["vpn"] == {"conditionalBackhaul": False}
    assert {"ospf", "syslog"} <= set(ds)


def test_the_helper_defaults_are_not_shared_between_entries():
    first = segment_helpers.qos_entry(GUEST)
    first["defaults"][0]["name"] = "changed"
    assert segment_helpers.qos_entry(GUEST)["defaults"][0]["name"] == DEFAULT_NAMES[0]


def test_the_rows_of_profile_rows_validate_as_the_modules_of_a_profile():
    rows = segment_helpers.profile_rows(
        [segment_helpers.ds_entry(GLOBAL), segment_helpers.ds_entry(GUEST)],
        [segment_helpers.qos_entry(GLOBAL), segment_helpers.qos_entry(GUEST)],
        [
            segment_helpers.firewall_entry(GLOBAL),
            segment_helpers.firewall_entry(GUEST),
        ],
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "P", "id": segment_helpers.CONFIG, "modules": rows}
    )
    assert isinstance(profile.device_settings, DeviceSettingsConfigurationModule)
    assert isinstance(profile.qos, QosConfigurationModule)
    assert isinstance(profile.firewall, FirewallConfigurationModule)
    assert [r["configurationId"] for r in rows] == [segment_helpers.CONFIG] * 3
    assert [r["id"] for r in rows] == [
        segment_helpers.DS_ID,
        segment_helpers.QOS_ID,
        segment_helpers.FW_ID,
    ]
    assert len(profile.device_settings.data.segments) == 2
    assert len(profile.qos.data.segments) == 2
    assert len(profile.firewall.data.segments) == 2


def test_profile_rows_refs_default_to_a_segment_row_per_entry():
    rows = segment_helpers.profile_rows(
        [segment_helpers.ds_entry(GLOBAL), segment_helpers.ds_entry(GUEST)], [], []
    )
    refs = rows[0]["refs"]["deviceSettings:segment"]
    assert [r["logicalId"] for r in refs] == [GLOBAL[2], GUEST[2]]
    assert [r["enterpriseObjectId"] for r in refs] == [GLOBAL[3], GUEST[3]]
    one = segment_helpers.profile_rows([segment_helpers.ds_entry(GLOBAL)], [], [])
    assert one[0]["refs"]["deviceSettings:segment"]["logicalId"] == GLOBAL[2]
    none = segment_helpers.profile_rows([], [], [])
    assert none[0]["refs"] == {}


def test_profile_rows_takes_the_ds_refs_given():
    ds = [segment_helpers.ds_entry(GLOBAL)]
    assert "refs" not in segment_helpers.profile_rows(ds, [], [], ds_refs=MISSING)[0]
    refs = {"deviceSettings:segment": {}}
    assert segment_helpers.profile_rows(ds, [], [], ds_refs=refs)[0]["refs"] == refs


async def test_the_registry_resolves_the_segments_given():
    registry = await segment_helpers.segment_registry(GLOBAL, GUEST)
    guest = await registry.resolve(SegmentIdentity, "guest")
    assert guest == segment_helpers.identity(GUEST)
