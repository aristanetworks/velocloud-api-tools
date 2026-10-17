"""Tests for Alias marker field validation and serialization behavior."""

import json
from typing import Annotated

from pydantic_core import MISSING
from pyvelo.client.schemas.license import GetEnterpriseEdgeLicenses
from pyvelo.core import Alias, VcoModel


class ModelWithAlias(VcoModel):
    dh_group: Annotated[int, Alias("DHGroup")] = 0
    syslog_forwarding: Annotated[bool | None | MISSING, Alias("syslog_forwarding")] = (
        MISSING
    )
    normal_field: str = "default"


class TestAliasMarkerValidation:
    def test_validates_from_vco_key(self):
        """Model should accept VCO API keys during validation."""
        data = {"DHGroup": 14, "normalField": "test"}
        obj = ModelWithAlias.model_validate(data)
        assert obj.dh_group == 14
        assert obj.normal_field == "test"

    def test_validates_from_python_name(self):
        """Model should also accept Python field names (validate_by_name)."""
        obj = ModelWithAlias(dh_group=14, normal_field="test")
        assert obj.dh_group == 14

    def test_serializes_to_vco_key(self):
        """Serialization should produce VCO API keys."""
        obj = ModelWithAlias(dh_group=14, normal_field="test")
        dumped = obj.model_dump(by_alias=True)
        assert "DHGroup" in dumped
        assert dumped["DHGroup"] == 14
        # normal_field uses to_camel alias generator
        assert "normalField" in dumped

    def test_json_serializes_to_vco_key(self):
        """JSON serialization should produce VCO API keys."""
        obj = ModelWithAlias(dh_group=14)
        raw = json.loads(obj.model_dump_json(by_alias=True))
        assert "DHGroup" in raw

    def test_alias_missing_default(self):
        """Alias marker with MISSING default should behave like other MISSING fields."""
        data = {"normalField": "test"}
        obj = ModelWithAlias.model_validate(data)
        assert obj.syslog_forwarding is MISSING

    def test_alias_round_trip(self):
        """Validate from VCO key, serialize back to VCO key."""
        data = {"DHGroup": 21, "syslog_forwarding": True, "normalField": "hi"}
        obj = ModelWithAlias.model_validate(data)
        dumped = obj.model_dump(by_alias=True)
        assert dumped["DHGroup"] == 21
        assert dumped["syslog_forwarding"] is True


class TestReservedKeywordAlias:
    def test_get_enterprise_edge_licenses_defaults_to_counts(self):
        request = GetEnterpriseEdgeLicenses(enterprise_id=1808)

        assert request.model_dump(by_alias=True) == {
            "enterpriseId": 1808,
            "with": ["counts"],
        }

    def test_get_enterprise_edge_licenses_serializes_with_counts(self):
        request = GetEnterpriseEdgeLicenses(enterprise_id=1808, with_=["counts"])

        assert request.model_dump(by_alias=True) == {
            "enterpriseId": 1808,
            "with": ["counts"],
        }

    def test_get_enterprise_edge_licenses_accepts_vco_with_alias(self):
        request = GetEnterpriseEdgeLicenses.model_validate(
            {"enterpriseId": 1808, "with": ["counts"]}
        )

        assert request.with_ == ["counts"]
