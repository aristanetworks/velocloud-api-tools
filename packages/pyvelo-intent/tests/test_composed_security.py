"""The keys the composer adds to a firewall rule, expected from the security objects its group names."""

from collections.abc import Mapping
from typing import Any

from pyvelo.client.enums.enterprise import SecurityObjectType
from pyvelo_intent.config.composed_security import (
    SECURITY_OBJECT_TYPES,
    SYNTHETIC_RULE_KEYS,
    SecurityObjects,
    expected_rule_keys,
)

GROUP = "6f1d2c3a-0b4e-4c55-9a77-1e2d3c4b5a69"
UNRESOLVED = (
    "the rule's security service group, or an object it names, "
    + "is not among the enterprise's security objects"
)

CATEGORY_DATA: dict[str, Any] = {
    "allowAndLogCategories": [11, 12],
    "blockedCategories": [21],
    "unknownCategoryAction": "allow",
}
REPUTATION_DATA: dict[str, Any] = {
    "minReputationScore": 40,
    "allowAndLogReputations": [3],
    "unknownReputationAction": "block",
}
IDPS_DATA: dict[str, Any] = {
    "idsEnabled": True,
    "ipsEnabled": False,
    "logEnabled": True,
}


def row(
    type_: str, id_: int, logical_id: str, data: Mapping[str, Any]
) -> dict[str, Any]:
    return {"type": type_, "id": id_, "logicalId": logical_id, "data": dict(data)}


def group_row(data: Mapping[str, Any], logical_id: str = GROUP) -> dict[str, Any]:
    return row("securityServiceGroup", 100, logical_id, data)


def objects(*extra: dict[str, Any], group: Mapping[str, Any] | None = None):
    named = {
        "urlCategoryFiltering": {"id": 7},
        "urlReputationFiltering": {"id": 8},
        "idps": {"id": 9},
    }
    return SecurityObjects.from_rows(
        [
            group_row(named if group is None else group),
            row("urlCategoryFiltering", 7, "c0", CATEGORY_DATA),
            row("urlReputationFiltering", 8, "c1", REPUTATION_DATA),
            row("idps", 9, "c2", IDPS_DATA),
            *extra,
        ]
    )


def test_the_constants_name_the_keys_and_the_types_read():
    assert SYNTHETIC_RULE_KEYS == (
        "atp_action",
        "urlCategoryFiltering",
        "urlReputationFiltering",
    )
    assert SECURITY_OBJECT_TYPES == (
        SecurityObjectType.SECURITY_SERVICE_GROUP,
        SecurityObjectType.URL_CATEGORY_FILTERING,
        SecurityObjectType.URL_REPUTATION_FILTERING,
        SecurityObjectType.IDPS,
    )


def test_the_url_filters_are_the_named_objects_data():
    expected = expected_rule_keys(GROUP, objects())

    assert expected.unresolved is None
    assert expected.values["urlCategoryFiltering"] == CATEGORY_DATA
    assert expected.values["urlReputationFiltering"] == REPUTATION_DATA


def test_atp_action_follows_the_named_idps_object():
    expected = expected_rule_keys(GROUP, objects())
    assert expected.values["atp_action"] == {
        "atp_enabled": True,
        "ids_enabled": True,
        "ips_enabled": False,
        "atp_logging_enabled": True,
    }

    off = SecurityObjects.from_rows(
        [
            group_row({"idps": {"id": 9}}),
            row(
                "idps",
                9,
                "c2",
                {"idsEnabled": False, "ipsEnabled": True, "logEnabled": False},
            ),
        ]
    )
    assert expected_rule_keys(GROUP, off).values["atp_action"] == {
        "atp_enabled": True,
        "ids_enabled": False,
        "ips_enabled": True,
        "atp_logging_enabled": False,
    }


def test_the_group_is_found_by_its_logical_id_in_any_case():
    expected = expected_rule_keys(GROUP.upper(), objects())
    assert expected.unresolved is None
    assert set(expected.values) == set(SYNTHETIC_RULE_KEYS)


def test_a_group_without_a_kind_expects_no_key_for_it():
    only_urls = objects(group={"urlCategoryFiltering": {"id": 7}})
    expected = expected_rule_keys(GROUP, only_urls)

    assert expected.unresolved is None
    assert set(expected.values) == {"urlCategoryFiltering"}


def test_a_malicious_ip_object_the_group_names_expects_no_key():
    named = objects(
        row("maliciousIpFiltering", 5, "c3", {"anything": 1}),
        group={"maliciousIpFiltering": {"id": 5}},
    )
    expected = expected_rule_keys(GROUP, named)
    assert expected.values == {}
    assert expected.unresolved is None


def test_an_unknown_group_or_object_is_unresolved_with_its_reason():
    unknown_group = expected_rule_keys(
        "00000000-0000-4000-8000-000000000000", objects()
    )
    assert unknown_group.values == {}
    assert unknown_group.unresolved == UNRESOLVED

    missing_object = objects(
        group={"urlCategoryFiltering": {"id": 99}, "idps": {"id": 9}}
    )
    expected = expected_rule_keys(GROUP, missing_object)
    assert expected.unresolved == UNRESOLVED
    # What can be expected still is.
    assert set(expected.values) == {"atp_action"}


def test_the_objects_are_looked_up_by_type_and_id():
    found = objects()
    assert found.group(GROUP) is not None
    assert found.group("00000000-0000-4000-8000-000000000000") is None
    assert found.named("idps", 9) is not None
    assert found.named("urlCategoryFiltering", 9) is None
    assert found.named("idps", 7) is None
