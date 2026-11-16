"""Tests for the validation framework: types, registry, decorator, and pipeline integration."""

from datetime import UTC, datetime
from typing import TypedDict

import pytest
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    ValidatorRegistry,
    validator,
)
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState

_TS = datetime(2024, 1, 1, tzinfo=UTC)


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


def _mod_kwargs() -> _ModuleKwargs:
    return _ModuleKwargs(
        created=_TS,
        modified=_TS,
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


# --- ValidationResult tests ---


class TestValidationResult:
    def test_empty_is_valid(self):
        r = ValidationResult()
        assert r.is_valid is True

    def test_with_errors_is_invalid(self):
        r = ValidationResult(
            errors=[ValidationError(path="x", message="bad", code="err")]
        )
        assert r.is_valid is False

    def test_with_only_warnings_is_valid(self):
        r = ValidationResult(
            warnings=[
                ValidationError(path="x", message="warn", code="w", severity="warning")
            ]
        )
        assert r.is_valid is True

    def test_merge(self):
        r1 = ValidationResult(errors=[ValidationError(path="a", message="a", code="a")])
        r2 = ValidationResult(
            errors=[ValidationError(path="b", message="b", code="b")],
            warnings=[
                ValidationError(path="c", message="c", code="c", severity="warning")
            ],
        )
        r1.merge(r2)
        assert len(r1.errors) == 2
        assert len(r1.warnings) == 1


# --- ValidatorRegistry tests ---


class TestValidatorRegistry:
    def test_register_and_run(self):
        registry = ValidatorRegistry()

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["write"], registry=registry
        )
        def my_validator(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="test", message="fail", code="test")]
            )

        assert my_validator.modules == [ConfigurationModuleName.FIREWALL]
        state = _make_state([])
        result = registry.run(state, phase="write")
        assert len(result.errors) == 1

    def test_phase_filtering(self):
        registry = ValidatorRegistry()

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["write"], registry=registry
        )
        def write_only(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="w", message="w", code="w")]
            )

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["read"], registry=registry
        )
        def read_only(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="r", message="r", code="r")]
            )

        assert write_only.phases == ["write"]
        assert read_only.phases == ["read"]
        state = _make_state([])

        # Only write-phase validator should run
        result = registry.run(state, phase="write")
        assert len(result.errors) == 1
        assert result.errors[0].path == "w"

        # Only read-phase validator should run
        result = registry.run(state, phase="read")
        assert len(result.errors) == 1
        assert result.errors[0].path == "r"

    def test_module_filtering(self):
        registry = ValidatorRegistry()

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["write"], registry=registry
        )
        def fw_validator(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="fw", message="fw", code="fw")]
            )

        @validator(
            module=ConfigurationModuleName.QOS, phases=["write"], registry=registry
        )
        def qos_validator(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="qos", message="qos", code="qos")]
            )

        assert fw_validator.modules == [ConfigurationModuleName.FIREWALL]
        assert qos_validator.modules == [ConfigurationModuleName.QOS]
        state = _make_state([])

        # Only firewall validator
        result = registry.run(
            state, phase="write", modules=[ConfigurationModuleName.FIREWALL]
        )
        assert len(result.errors) == 1
        assert result.errors[0].path == "fw"

        # Only QOS validator
        result = registry.run(
            state, phase="write", modules=[ConfigurationModuleName.QOS]
        )
        assert len(result.errors) == 1
        assert result.errors[0].path == "qos"

    def test_multi_module_validator(self):
        registry = ValidatorRegistry()

        @validator(
            modules=[ConfigurationModuleName.FIREWALL, ConfigurationModuleName.QOS],
            phases=["write"],
            registry=registry,
        )
        def cross_validator(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="cross", message="cross", code="cross")]
            )

        assert cross_validator.modules == [
            ConfigurationModuleName.FIREWALL,
            ConfigurationModuleName.QOS,
        ]
        state = _make_state([])

        # Should match when any listed module is requested
        result = registry.run(
            state, phase="write", modules=[ConfigurationModuleName.FIREWALL]
        )
        assert len(result.errors) == 1

        result = registry.run(
            state, phase="write", modules=[ConfigurationModuleName.QOS]
        )
        assert len(result.errors) == 1

        # Should not match when a different module is requested
        result = registry.run(
            state, phase="write", modules=[ConfigurationModuleName.WAN]
        )
        assert len(result.errors) == 0

    def test_no_module_filter_runs_all(self):
        registry = ValidatorRegistry()

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["write"], registry=registry
        )
        def v1(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="v1", message="v1", code="v1")]
            )

        @validator(
            module=ConfigurationModuleName.QOS, phases=["write"], registry=registry
        )
        def v2(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="v2", message="v2", code="v2")]
            )

        assert v1.modules == [ConfigurationModuleName.FIREWALL]
        assert v2.modules == [ConfigurationModuleName.QOS]
        state = _make_state([])
        result = registry.run(state, phase="write", modules=None)
        assert len(result.errors) == 2


# --- Pipeline validation integration tests ---


class TestPipelineValidation:
    def test_validate_standalone(self):
        registry = ValidatorRegistry()

        @validator(
            module=ConfigurationModuleName.FIREWALL, phases=["write"], registry=registry
        )
        def always_fail(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="test", message="fail", code="test")]
            )

        assert always_fail.phases == ["write"]
        pipeline = ConfigPipeline(client=None, validator_registry=registry)
        state = _make_state([])
        result = pipeline.validate(state)
        assert not result.is_valid
        assert len(result.errors) == 1

    @pytest.mark.anyio
    async def test_save_blocked_by_validation_errors(self):
        registry = ValidatorRegistry()

        @validator(modules=[], phases=["write"], registry=registry)
        def always_fail(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="test", message="blocked", code="test")]
            )

        assert always_fail.phases == ["write"]
        pipeline = ConfigPipeline(client=None, validator_registry=registry)

        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(segments=[]),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        state.take_snapshot()

        # Modify the data to trigger a change
        state.profile_config.firewall.data.firewall_enabled = False

        result = await pipeline.save(state)
        assert not result.is_valid
        assert len(result.validation_errors) == 1
        assert result.modules_updated == []  # save was blocked

    @pytest.mark.anyio
    async def test_save_with_validation_disabled(self):
        registry = ValidatorRegistry()

        @validator(modules=[], phases=["write"], registry=registry)
        def always_fail(_state: ConfigStateLike) -> ValidationResult:
            return ValidationResult(
                errors=[ValidationError(path="test", message="blocked", code="test")]
            )

        assert always_fail.phases == ["write"]
        pipeline = ConfigPipeline(client=None, validator_registry=registry)

        fw_mod = FirewallConfigurationModule(
            name="firewall",
            data=FirewallData(segments=[]),
            id_=1,
            **_mod_kwargs(),
        )
        state = _make_state([fw_mod])
        state.take_snapshot()

        state.profile_config.firewall.data.firewall_enabled = False

        # With validate=False, save proceeds (dry_run to avoid needing a real client)
        result = await pipeline.save(state, validate=False, dry_run=True)
        assert result.is_valid
        assert result.modules_updated == ["firewall"]
