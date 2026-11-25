"""Validation framework: types, registry, and decorator for incremental config validation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pyvelo.client.enums.configuration import ConfigurationModuleName


@runtime_checkable
class ConfigStateLike(Protocol):
    """Minimal protocol for the state object validators read.

    The pipeline's state is ``pyvelo.config.state.ConfigState``; this protocol
    keeps the framework decoupled from any specific implementation.

    ``target_type`` is exposed as a read-only ``@property`` so that concrete
    states may narrow it to a ``Literal[...]`` (e.g. ``"edge" | "profile"``).
    Mutable Protocol attributes are invariant, which would reject such
    narrowing.
    """

    @property
    def target_type(self) -> str:
        """What the state is of, such as ``"edge"`` or ``"profile"``."""
        ...

    edge_config: Any
    profile_config: Any


@dataclass
class ValidationError:
    """A validation error with location, message, and machine-readable code."""

    path: str
    message: str
    code: str
    severity: str = "error"


@dataclass
class ValidationResult:
    """Aggregated result of running one or more validators."""

    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether no validator reported an error; warnings do not count."""
        return len(self.errors) == 0

    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


@runtime_checkable
class Validator(Protocol):
    """Protocol for config validators."""

    phases: list[str]
    modules: list[ConfigurationModuleName]

    def validate(self, state: ConfigStateLike) -> ValidationResult:
        """The errors and warnings this validator finds in *state*."""
        ...


class ValidatorRegistry:
    """Registry for validators, with phase/module-scoped execution."""

    def __init__(self) -> None:
        """A registry with no validators."""
        self._validators: list[Validator] = []

    def register(self, v: Validator) -> None:
        """Add *v*, after the validators already registered; ``run`` keeps the order."""
        self._validators.append(v)

    def run(
        self,
        state: ConfigStateLike,
        *,
        phase: str,
        modules: list[ConfigurationModuleName] | None = None,
    ) -> ValidationResult:
        """Run all matching validators and return aggregated result."""
        result = ValidationResult()
        for v in self._validators:
            if phase not in v.phases:
                continue
            # Empty v.modules means "applies to all modules"
            if (
                modules is not None
                and v.modules
                and not any(m in v.modules for m in modules)
            ):
                continue
            result.merge(v.validate(state))
        return result


@dataclass
class _DecoratedValidator:
    """Concrete validator created by the @validator decorator."""

    phases: list[str]
    modules: list[ConfigurationModuleName]
    _fn: Callable[[ConfigStateLike], ValidationResult]

    def validate(self, state: ConfigStateLike) -> ValidationResult:
        return self._fn(state)


def validator(
    *,
    module: ConfigurationModuleName | None = None,
    modules: list[ConfigurationModuleName] | None = None,
    phases: list[str],
    registry: ValidatorRegistry | None = None,
) -> Callable[[Callable[[ConfigStateLike], ValidationResult]], _DecoratedValidator]:
    """Decorator to create and optionally register a validator from a function.

    Usage:
        @validator(module="deviceSettings", phases=["read", "write"])
        def validate_no_overlapping_subnets(state: ConfigStateLike) -> ValidationResult:
            ...
    """
    resolved_modules: list[ConfigurationModuleName] = []
    if modules is not None:
        resolved_modules = modules
    elif module is not None:
        resolved_modules = [module]

    def decorator(
        fn: Callable[[ConfigStateLike], ValidationResult],
    ) -> _DecoratedValidator:
        v = _DecoratedValidator(
            phases=phases,
            modules=resolved_modules,
            _fn=fn,
        )
        if registry is not None:
            registry.register(v)
        return v

    return decorator
