"""Convenience re-exports for control plane configuration types.

Usage:
    from pyvelo_intent.config.control_plane import ControlPlaneData, GatewaySelection
"""

from pyvelo.client.models.configuration.control_plane.root import (
    ControlPlaneData,
    GatewaySelection,
)

__all__ = [
    "ControlPlaneData",
    "GatewaySelection",
]
