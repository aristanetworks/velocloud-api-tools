"""Tests for DefaultingModel type annotation.

DefaultingModel is an Annotated marker that catches validation errors and
silently falls back to a default instance (via default_factory) instead of
raising a ValidationError.  It also records audit entries when validation
context contains an "audit" list.
"""

from typing import Annotated, Any

from pydantic import Field
from pydantic_core import MISSING
from pyvelo.core import Alias, DefaultingModel, VcoModel

_COMPACT = {"compact": True}

# ---------------------------------------------------------------------------
# Test models (inspired by device_settings patterns)
# ---------------------------------------------------------------------------


class LinkSettings(VcoModel):
    autonegotiation: bool = True
    speed: str = "100M"
    mtu: int = 1500


class MulticastSettings(VcoModel):
    enabled: bool = False
    type_: Annotated[str, Alias("type")] = "IGMP_V2"


class DeviceConfig(VcoModel):
    name: str = ""
    link: Annotated[LinkSettings, DefaultingModel(LinkSettings)] = Field(
        default_factory=LinkSettings
    )
    multicast: Annotated[MulticastSettings, DefaultingModel(MulticastSettings)] = Field(
        default_factory=MulticastSettings
    )


class WithAlias(VcoModel):
    net: Annotated[
        LinkSettings, DefaultingModel(LinkSettings), Alias("networkSettings")
    ] = Field(default_factory=LinkSettings)


class WithOptional(VcoModel):
    """Model where DefaultingModel field sits alongside MISSING-typed fields."""

    tag: str | MISSING = MISSING
    link: Annotated[LinkSettings, DefaultingModel(LinkSettings)] = Field(
        default_factory=LinkSettings
    )


def _validate_device_config(**data: Any) -> DeviceConfig:
    """Validate raw inputs through Pydantic, including intentionally invalid values."""
    return DeviceConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Valid data
# ---------------------------------------------------------------------------


class TestValidData:
    def test_valid_dict_validates_normally(self):
        m = _validate_device_config(
            name="edge1",
            link={"autonegotiation": False, "speed": "1000M", "mtu": 9000},  # type: ignore[arg-type]
            multicast={"enabled": True},  # type: ignore[arg-type]
        )
        assert m.link.autonegotiation is False
        assert m.link.speed == "1000M"
        assert m.link.mtu == 9000
        assert m.multicast.enabled is True

    def test_model_instance_passes_through(self):
        link = LinkSettings(speed="2500M")
        m = DeviceConfig(link=link, multicast=MulticastSettings())
        assert m.link.speed == "2500M"

    def test_partial_dict_fills_model_defaults(self):
        m = _validate_device_config(link={"mtu": 9000}, multicast={})
        assert m.link.autonegotiation is True
        assert m.link.speed == "100M"
        assert m.link.mtu == 9000
        assert m.multicast.enabled is False
        assert m.multicast.type_ == "IGMP_V2"

    def test_empty_dict_produces_default_values(self):
        m = _validate_device_config(link={}, multicast={})
        assert m.link == LinkSettings()
        assert m.multicast == MulticastSettings()


# ---------------------------------------------------------------------------
# Invalid data -> falls back to default
# ---------------------------------------------------------------------------


class TestInvalidDataFallback:
    def test_wrong_type_produces_default(self):
        """A non-dict/non-model value should fail validation and use default."""
        m = _validate_device_config(link="garbage", multicast=42)
        assert m.link == LinkSettings()
        assert m.multicast == MulticastSettings()

    def test_none_produces_default(self):
        m = _validate_device_config(link=None, multicast=None)
        assert m.link == LinkSettings()
        assert m.multicast == MulticastSettings()

    def test_list_produces_default(self):
        m = _validate_device_config(link=[1, 2, 3], multicast=[])
        assert m.link == LinkSettings()
        assert m.multicast == MulticastSettings()

    def test_invalid_field_values_produce_default(self):
        """If nested field validation fails, the whole sub-model is defaulted."""
        m = _validate_device_config(
            link={"mtu": "not_a_number"},  # type: ignore[arg-type]
            multicast={"enabled": "not_coercible"},  # type: ignore[arg-type]
        )
        assert m.link == LinkSettings()
        assert m.multicast == MulticastSettings()


# ---------------------------------------------------------------------------
# Default factory (no data provided)
# ---------------------------------------------------------------------------


class TestDefaultFactory:
    def test_omitted_field_gets_default_instance(self):
        m = DeviceConfig()
        assert isinstance(m.link, LinkSettings)
        assert isinstance(m.multicast, MulticastSettings)
        assert m.link == LinkSettings()

    def test_each_instance_gets_fresh_default(self):
        """Default factory should produce independent instances."""
        m1 = DeviceConfig()
        m2 = DeviceConfig()
        assert m1.link is not m2.link
        assert m1.link == m2.link


# ---------------------------------------------------------------------------
# Alias support
# ---------------------------------------------------------------------------


class TestAlias:
    def test_alias_accepted_on_input(self):
        m = WithAlias.model_validate(
            {"networkSettings": {"speed": "1000M", "mtu": 9000}}
        )
        assert m.net.speed == "1000M"
        assert m.net.mtu == 9000

    def test_alias_invalid_data_produces_default(self):
        m = WithAlias.model_validate({"networkSettings": "bad"})
        assert m.net == LinkSettings()

    def test_python_name_also_accepted(self):
        m = WithAlias.model_validate({"net": {"speed": "2500M"}})
        assert m.net.speed == "2500M"


# ---------------------------------------------------------------------------
# Audit context
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_entry_recorded_on_failure(self):
        audit: list[dict[str, Any]] = []
        DeviceConfig.model_validate(
            {"link": "invalid", "multicast": {}},
            context={"audit": audit},
        )
        assert len(audit) == 1
        entry = audit[0]
        assert entry["field"] == "link"
        assert entry["bad_value"] == "invalid"
        assert entry["action"] == "set_to_default"
        assert "failed model validation" in entry["reason"]

    def test_no_audit_entry_on_valid_data(self):
        audit: list[dict[str, Any]] = []
        DeviceConfig.model_validate(
            {"link": {"mtu": 9000}, "multicast": {}},
            context={"audit": audit},
        )
        assert len(audit) == 0

    def test_multiple_failures_produce_multiple_entries(self):
        audit: list[dict[str, Any]] = []
        DeviceConfig.model_validate(
            {"link": None, "multicast": None},
            context={"audit": audit},
        )
        assert len(audit) == 2
        fields = {e["field"] for e in audit}
        assert fields == {"link", "multicast"}

    def test_no_audit_key_in_context_is_safe(self):
        """Validation should work even without audit in context."""
        m = DeviceConfig.model_validate(
            {"link": "bad"},
            context={"some_other_key": True},
        )
        assert m.link == LinkSettings()


# ---------------------------------------------------------------------------
# Interaction with compact serialization
# ---------------------------------------------------------------------------


class TestCompactSerialization:
    def test_default_instance_omitted_in_compact(self):
        m = DeviceConfig()
        d = m.model_dump(by_alias=False, context=_COMPACT)
        assert "link" not in d
        assert "multicast" not in d

    def test_non_default_kept_in_compact(self):
        m = DeviceConfig(link=LinkSettings(mtu=9000))
        d = m.model_dump(by_alias=False, context=_COMPACT)
        assert "link" in d
        assert d["link"]["mtu"] == 9000

    def test_fallen_back_default_omitted_in_compact(self):
        """A field that fell back to default via invalid data is still a default."""
        m = _validate_device_config(link="garbage", multicast={})
        d = m.model_dump(by_alias=False, context=_COMPACT)
        assert "link" not in d
        assert "multicast" not in d


# ---------------------------------------------------------------------------
# Coexistence with other field types
# ---------------------------------------------------------------------------


class TestCoexistence:
    def test_alongside_missing_field(self):
        m = WithOptional(tag="hello")
        assert m.tag == "hello"
        assert m.link == LinkSettings()

    def test_missing_field_stays_missing(self):
        m = WithOptional()
        assert m.tag is MISSING
        assert isinstance(m.link, LinkSettings)
