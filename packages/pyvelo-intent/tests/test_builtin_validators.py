"""Tests for built-in validators: key_uniqueness, override_consistency, required_fields."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, TypedDict, cast

from layered_models import Mod, Rule, mod_json
from pydantic import Field
from pyvelo.client.enums.configuration import ConfigurationModuleType
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.device_settings.lan import (
    LanSettings,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    SegmentRoutes,
    StaticRoute,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.validation import ConfigStateLike
from pyvelo.core import (
    ByPresence,
    Default,
    Edge,
    Inheritable,
    KeyField,
    Layered,
    Profile,
    VcoModel,
)
from pyvelo.core.layers import L
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.config.validators.builtin import (
    key_uniqueness,
    override_consistency,
    required_fields,
)


class _ModuleKwargs(TypedDict):
    created: datetime
    modified: datetime
    type_: ConfigurationModuleType
    description: str | None
    configuration_id: int
    enterprise_logical_id: None
    schema_version: None
    version: None
    metadata: None


def _seg_meta(name: str = "Global Segment", segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
    )


def _mod_kwargs() -> _ModuleKwargs:
    return _ModuleKwargs(
        created=datetime(2024, 1, 1, tzinfo=UTC),
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=100,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(modules: list[AnyConfigurationModule]) -> ConfigState:
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(
            name="Test Profile", id_=1, modules=modules
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


class TestKeyUniqueness:
    def test_unique_keys_pass(self):
        """No errors when all keys are unique."""
        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(
                segments=[
                    FirewallSegment(
                        segment=_seg_meta("Global Segment"),
                        outbound=[
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "rule-1",
                                    "match": {},
                                    "action": {"allow_or_deny": "allow"},
                                }
                            ),
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "rule-2",
                                    "match": {},
                                    "action": {"allow_or_deny": "deny"},
                                }
                            ),
                        ],
                    )
                ]
            ),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        result = key_uniqueness.validate(state)
        assert result.is_valid

    def test_duplicate_segment_keys(self):
        """Detect duplicate segment names."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(),
                segments=[
                    DeviceSegment(segment=_seg_meta("Global Segment", 0)),
                    DeviceSegment(segment=_seg_meta("Global Segment", 1)),
                ],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = key_uniqueness.validate(state)
        assert not result.is_valid
        assert any("Duplicate key" in e.message for e in result.errors)

    def test_duplicate_firewall_rule_names(self):
        """Detect duplicate outbound rule names via KeyField."""
        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(
                segments=[
                    FirewallSegment(
                        segment=_seg_meta(),
                        outbound=[
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "same",
                                    "match": {},
                                    "action": {"allow_or_deny": "allow"},
                                }
                            ),
                            FirewallOutboundRule.model_validate(
                                {
                                    "name": "same",
                                    "match": {},
                                    "action": {"allow_or_deny": "deny"},
                                }
                            ),
                        ],
                    )
                ]
            ),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        result = key_uniqueness.validate(state)
        assert not result.is_valid

    def test_empty_config(self):
        """No errors on empty config."""
        state = _make_state([])
        result = key_uniqueness.validate(state)
        assert result.is_valid


def _static_route(gateway: str) -> StaticRoute:
    return StaticRoute(
        destination="10.0.0.0",
        netmask="255.255.255.0",
        gateway=gateway,
        cidr_prefix=24,
        wan_interface="GE3",
    )


def _routes_state(*routes: StaticRoute) -> ConfigState:
    ds_mod = DeviceSettingsConfigurationModule(
        name="deviceSettings",
        data=DeviceSettingsData(
            lan=LanSettings(),
            segments=[
                DeviceSegment(
                    segment=_seg_meta(), routes=SegmentRoutes(static=list(routes))
                )
            ],
        ),
        id_=2,
        **_mod_kwargs(),
    )
    return _make_state([ds_mod])


def test_key_uniqueness_reads_composite_keys():
    result = key_uniqueness.validate(
        _routes_state(_static_route("192.168.1.1"), _static_route("192.168.1.1"))
    )
    assert [(e.path, e.code) for e in result.errors] == [
        ("deviceSettings.segments[0].routes.static[1]", "key_uniqueness")
    ]
    assert '"10.0.0.0",24,"192.168.1.1",GE3' in result.errors[0].message

    result = key_uniqueness.validate(
        _routes_state(_static_route("192.168.1.1"), _static_route("192.168.1.2"))
    )
    assert result.is_valid


def test_key_uniqueness_compares_key_parts_in_string_form():
    as_text = _static_route("192.168.1.1")
    as_text.cidr_prefix = "24"
    result = key_uniqueness.validate(
        _routes_state(_static_route("192.168.1.1"), as_text)
    )
    assert [e.path for e in result.errors] == [
        "deviceSettings.segments[0].routes.static[1]"
    ]


class TestOverrideConsistency:
    def test_no_errors_on_valid_config(self):
        """Override consistency should not error on well-formed config."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(),
                segments=[DeviceSegment(segment=_seg_meta())],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = override_consistency.validate(state)
        # Override consistency produces warnings, not errors
        assert result.is_valid

    def test_empty_config(self):
        """No errors on empty config."""
        state = _make_state([])
        result = override_consistency.validate(state)
        assert result.is_valid


class TestRequiredFields:
    def test_valid_config_passes(self):
        """No errors when all required fields are filled."""
        ds_mod = DeviceSettingsConfigurationModule(
            name="deviceSettings",
            data=DeviceSettingsData(
                lan=LanSettings(),
                segments=[DeviceSegment(segment=_seg_meta())],
            ),
            id_=2,
            **_mod_kwargs(),
        )
        state = _make_state([ds_mod])
        result = required_fields.validate(state)
        assert result.is_valid

    def test_empty_config(self):
        """No errors on empty config."""
        state = _make_state([])
        result = required_fields.validate(state)
        assert result.is_valid


class _Holder(VcoModel):
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)


class _Held(Layered[L]):
    holder: Annotated[Inheritable[_Holder], ByPresence(), Default(factory=_Holder)] = (
        Field(default=...)
    )


def _layered_state(data: object, target: str) -> ConfigStateLike:
    """A state whose one module holds *data*, a model of the layer *target*."""
    config = SimpleNamespace(modules=[SimpleNamespace(name="held", data=data)])
    state = SimpleNamespace(
        target_type=target,
        edge_config=config if target == "edge" else None,
        profile_config=config,
    )
    return cast(ConfigStateLike, cast(object, state))


class TestLayeredModels:
    def test_an_inheritable_field_is_not_required(self):
        """Where the edge inherits (it stores nothing) or the profile reads its display default."""
        for target, data in (
            ("edge", Mod[Edge].model_validate(mod_json())),
            ("profile", Mod[Profile].model_validate(mod_json())),
        ):
            assert required_fields.validate(_layered_state(data, target)).is_valid

    def test_a_duplicate_key_inside_an_inheritable_value_is_found(self):
        raw = {"holder": {"rules": [{"name": "r1"}, {"name": "r1"}]}}
        for target, data in (
            ("edge", _Held[Edge].model_validate(raw)),
            ("profile", _Held[Profile].model_validate(raw)),
        ):
            result = key_uniqueness.validate(_layered_state(data, target))
            assert [error.path for error in result.errors] == ["held.holder.rules[1]"]
            assert result.errors[0].code == "key_uniqueness"
