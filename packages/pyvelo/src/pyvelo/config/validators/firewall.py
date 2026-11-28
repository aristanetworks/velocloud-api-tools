"""Domain-specific validators for firewall and QOS modules."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import MISSING

from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    python_module_name,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    validator,
)
from pyvelo.core import named_path
from pyvelo.core.rebase import Path, PathPart

if TYPE_CHECKING:
    from pyvelo.client.schemas.configuration import (
        EdgeConfiguration,
        EnterpriseConfigurationProfile,
        FirewallConfigurationModule,
        QosConfigurationModule,
    )


@dataclass(frozen=True, slots=True)
class _RulePlace:
    """A rule of a segment of a module's ``segments``.

    ``segment`` is the segment's position in ``segments``, ``rules`` the name
    of the segment's list of rules, and ``rule`` the rule's position in it.
    """

    module: "FirewallConfigurationModule[Any] | QosConfigurationModule[Any]"
    segment: int
    rules: str
    rule: int


def _rule_finding(
    place: _RulePlace,
    message: str,
    code: str,
    *,
    severity: str,
    field: str | None = None,
) -> ValidationError:
    """Report a finding at the rule at *place*, or at its *field* if one is given."""
    parts: list[PathPart] = ["segments", place.segment, place.rules, place.rule]
    if field is not None:
        parts.append(field)
    return ValidationError(
        path=named_path(Path(parts), place.module.data),
        message=message,
        code=code,
        severity=severity,
        module=python_module_name(place.module.name),
    )


def _duplicate_names(
    module: "FirewallConfigurationModule[Any] | QosConfigurationModule[Any]",
    list_name: str,
) -> Iterator[ValidationError]:
    """Report each rule whose name an earlier rule of its segment has."""
    for position, seg in enumerate(module.data.segments):
        seen_names: dict[str, int] = {}
        if hasattr(seg, list_name):
            for i, rule in enumerate(getattr(seg, list_name)):
                name = getattr(rule, "name", None)
                if name and name in seen_names:
                    yield _rule_finding(
                        _RulePlace(module, position, list_name, i),
                        message=(
                            f"Duplicate rule name {name!r} "
                            f"(first at index {seen_names[name]})"
                        ),
                        code="duplicate_rule_name",
                        severity="error",
                    )
                elif name:
                    seen_names[name] = i


@validator(
    modules=[ConfigurationModuleName.FIREWALL, ConfigurationModuleName.QOS],
    phases=["read", "write"],
)
def validate_rule_names_unique(state: ConfigStateLike) -> ValidationResult:
    """Check that rule names are unique within each segment (firewall and QOS)."""
    result = ValidationResult()

    if state.target_type == "edge" and state.edge_config is not None:
        config: EdgeConfiguration | EnterpriseConfigurationProfile = state.edge_config
    else:
        config = state.profile_config

    if config.modules is MISSING:
        return result

    # Check firewall rule names
    if config.has_module(ConfigurationModuleName.FIREWALL):
        fw = config.firewall
        if hasattr(fw.data, "segments"):
            result.errors.extend(_duplicate_names(fw, "outbound"))

    # Check QOS rule names
    if config.has_module(ConfigurationModuleName.QOS):
        qos = config.qos
        if hasattr(qos.data, "segments"):
            result.errors.extend(_duplicate_names(qos, "rules"))

    return result


def _known_interfaces(data: DeviceSettingsData) -> set[str]:
    """The names of the routed interfaces deviceSettings has."""
    known_interfaces: set[str] = set()

    if hasattr(data, "routed_interfaces") and data.routed_interfaces:
        for iface in data.routed_interfaces:
            name = getattr(iface, "name", None)
            if name:
                known_interfaces.add(name)

    return known_interfaces


def _unknown_interfaces(
    module: "FirewallConfigurationModule[Any]", known: set[str]
) -> Iterator[ValidationError]:
    """Report each interface a firewall rule names that is not in *known*."""
    for position, seg in enumerate(module.data.segments):
        if hasattr(seg, "outbound"):
            for i, rule in enumerate(seg.outbound):
                match = getattr(rule, "match", None)
                if match is None:
                    continue

                s_iface = getattr(match, "s_interface", "") or getattr(
                    match, "source_interface", ""
                )
                d_iface = getattr(match, "d_interface", "") or getattr(
                    match, "destination_interface", ""
                )

                for iface_name, direction in [
                    (s_iface, "source"),
                    (d_iface, "destination"),
                ]:
                    if iface_name and iface_name not in known:
                        yield _rule_finding(
                            _RulePlace(module, position, "outbound", i),
                            message=(
                                f"Rule references {direction} interface "
                                f"{iface_name!r} not found in device_settings"
                            ),
                            code="unknown_interface_ref",
                            field="match",
                            severity="warning",
                        )


@validator(
    modules=[ConfigurationModuleName.DEVICE_SETTINGS, ConfigurationModuleName.FIREWALL],
    phases=["write"],
)
def validate_interface_firewall_consistency(state: ConfigStateLike) -> ValidationResult:
    """Check that interfaces referenced in firewall rules exist in deviceSettings."""
    result = ValidationResult()

    if state.target_type == "edge" and state.edge_config is not None:
        config: EdgeConfiguration | EnterpriseConfigurationProfile = state.edge_config
    else:
        config = state.profile_config

    if config.modules is MISSING:
        return result

    if not config.has_module(
        ConfigurationModuleName.DEVICE_SETTINGS
    ) or not config.has_module(ConfigurationModuleName.FIREWALL):
        return result

    # Collect known interface names from deviceSettings
    ds = config.device_settings
    known_interfaces = _known_interfaces(ds.data)

    if not known_interfaces:
        return result

    # Check firewall rule references
    fw = config.firewall
    if hasattr(fw.data, "segments"):
        result.warnings.extend(_unknown_interfaces(fw, known_interfaces))

    return result
