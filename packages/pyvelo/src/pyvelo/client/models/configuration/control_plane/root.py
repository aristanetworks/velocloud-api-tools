"""The ``controlPlane`` module's data: the Gateway selection, primary and secondary."""

from typing import Literal

from pyvelo.core import VcoModel

GatewayMode = Literal["dynamic", "static"]


class GatewaySelection(VcoModel):
    primary: str
    secondary: str | None = None
    mode: GatewayMode


class ControlPlaneData(VcoModel):
    gateway_selection: GatewaySelection
