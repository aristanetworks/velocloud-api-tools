"""Domain-specific validators for firewall and QOS modules."""

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    validator,
)


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
            for seg in fw.data.segments:
                seg_name = seg.segment.name
                seen_firewall_names: dict[str, int] = {}
                if hasattr(seg, "outbound"):
                    for i, rule in enumerate(seg.outbound):
                        name = getattr(rule, "name", None)
                        if name and name in seen_firewall_names:
                            result.errors.append(
                                ValidationError(
                                    path=f"firewall.segments[{seg_name}].outbound[{i}]",
                                    message=f"Duplicate rule name {name!r} (first at index {seen_firewall_names[name]})",
                                    code="duplicate_rule_name",
                                )
                            )
                        elif name:
                            seen_firewall_names[name] = i

    # Check QOS rule names
    if config.has_module(ConfigurationModuleName.QOS):
        qos = config.qos
        if hasattr(qos.data, "segments"):
            for seg in qos.data.segments:
                seg_name = seg.segment.name
                seen_qos_names: dict[str, int] = {}
                if hasattr(seg, "rules"):
                    for i, rule in enumerate(seg.rules):
                        name = getattr(rule, "name", None)
                        if name and name in seen_qos_names:
                            result.errors.append(
                                ValidationError(
                                    path=f"QOS.segments[{seg_name}].rules[{i}]",
                                    message=f"Duplicate rule name {name!r} (first at index {seen_qos_names[name]})",
                                    code="duplicate_rule_name",
                                )
                            )
                        elif name:
                            seen_qos_names[name] = i

    return result


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
    known_interfaces: set[str] = set()

    if hasattr(ds.data, "routed_interfaces") and ds.data.routed_interfaces:
        for iface in ds.data.routed_interfaces:
            name = getattr(iface, "name", None)
            if name:
                known_interfaces.add(name)

    if not known_interfaces:
        return result

    # Check firewall rule references
    fw = config.firewall
    if hasattr(fw.data, "segments"):
        for seg in fw.data.segments:
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
                        if iface_name and iface_name not in known_interfaces:
                            result.warnings.append(
                                ValidationError(
                                    path=f"firewall.segments[{seg_name}].outbound[{i}].match",
                                    message=(
                                        f"Rule references {direction} interface "
                                        f"{iface_name!r} not found in deviceSettings"
                                    ),
                                    code="unknown_interface_ref",
                                    severity="warning",
                                )
                            )

    return result
