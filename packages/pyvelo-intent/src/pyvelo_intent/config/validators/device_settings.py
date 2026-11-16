"""Domain-specific validators for deviceSettings module."""

import ipaddress

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


def _parse_network(
    cidr_ip: str | None, cidr_prefix: int | None
) -> ipaddress.IPv4Network | None:
    """Try to parse a CIDR IP and prefix into an IPv4Network, or return None."""
    if not cidr_ip or cidr_prefix is None:
        return None
    try:
        return ipaddress.IPv4Network(f"{cidr_ip}/{cidr_prefix}", strict=False)
    except ValueError, TypeError:
        return None


@validator(module=ConfigurationModuleName.DEVICE_SETTINGS, phases=["read", "write"])
def validate_no_overlapping_subnets(state: ConfigStateLike) -> ValidationResult:
    """Check that VLAN subnets don't overlap within the same segment."""
    result = ValidationResult()

    if state.target_type == "edge" and state.edge_config is not None:
        config: EdgeConfiguration | EnterpriseConfigurationProfile = state.edge_config
    else:
        config = state.profile_config

    if config.modules is MISSING or not config.has_module(
        ConfigurationModuleName.DEVICE_SETTINGS
    ):
        return result

    ds = config.device_settings
    lan = ds.data.lan

    if not hasattr(lan, "networks") or not lan.networks:
        return result

    # Group networks by segment_id
    by_segment: dict[int, list[tuple[str, ipaddress.IPv4Network]]] = {}
    for net in lan.networks:
        network = _parse_network(
            getattr(net, "cidr_ip", None),
            getattr(net, "cidr_prefix", None),
        )
        if network is None:
            continue
        seg_id = getattr(net, "segment_id", 0)
        by_segment.setdefault(seg_id, []).append((net.name, network))

    # Check for overlaps within each segment
    for seg_id, nets in by_segment.items():
        for j, (name_j, net_j) in enumerate(nets):
            for name_k, net_k in nets[j + 1 :]:
                if net_j.overlaps(net_k):
                    result.errors.append(
                        ValidationError(
                            path="deviceSettings.lan.networks",
                            message=(
                                f"Network {name_j!r} ({net_j}) overlaps with "
                                f"{name_k!r} ({net_k}) in segment {seg_id}"
                            ),
                            code="overlapping_subnets",
                        )
                    )

    return result
