"""Domain-specific validators for firewall and QOS modules."""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from pydantic import MISSING

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    validator,
)

if TYPE_CHECKING:
    from pyvelo.client.schemas.configuration import (
        EdgeConfiguration,
        EnterpriseConfigurationProfile,
    )


def _rule_finding(
    rule: tuple[str, object, str, int],
    message: str,
    code: str,
    *,
    field: str | None = None,
    severity: str = "error",
) -> ValidationError:
    """Report a finding at a rule of a segment.

    *rule* is the rule's module, its segment's name, the rule list's name and
    the rule's index in it; *field* is the rule's field the finding is at, if
    it is at one.
    """
    module, segment_name, list_name, index = rule
    path = f"{module}.segments[{segment_name}].{list_name}[{index}]"
    if field is not None:
        path = f"{path}.{field}"
    return ValidationError(path=path, message=message, code=code, severity=severity)


def _duplicate_names(
    module: str,
    list_name: str,
    segments: Iterable[FirewallSegment[Any] | QosSegment[Any]],
) -> Iterator[ValidationError]:
    """Report each rule whose name an earlier rule of its segment has."""
    for seg in segments:
        seg_name = seg.segment.name
        seen_names: dict[str, int] = {}
        if hasattr(seg, list_name):
            for i, rule in enumerate(getattr(seg, list_name)):
                name = getattr(rule, "name", None)
                if name and name in seen_names:
                    yield _rule_finding(
                        (module, seg_name, list_name, i),
                        message=(
                            f"Duplicate rule name {name!r} "
                            f"(first at index {seen_names[name]})"
                        ),
                        code="duplicate_rule_name",
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
            result.errors.extend(
                _duplicate_names("firewall", "outbound", fw.data.segments)
            )

    # Check QOS rule names
    if config.has_module(ConfigurationModuleName.QOS):
        qos = config.qos
        if hasattr(qos.data, "segments"):
            result.errors.extend(_duplicate_names("QOS", "rules", qos.data.segments))

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
    segments: Iterable[FirewallSegment[Any]], known: set[str]
) -> Iterator[ValidationError]:
    """Report each interface a firewall rule names that is not in *known*."""
    for seg in segments:
        seg_name = seg.segment.name
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
                            ("firewall", seg_name, "outbound", i),
                            message=(
                                f"Rule references {direction} interface "
                                f"{iface_name!r} not found in deviceSettings"
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
        result.warnings.extend(_unknown_interfaces(fw.data.segments, known_interfaces))

    return result
