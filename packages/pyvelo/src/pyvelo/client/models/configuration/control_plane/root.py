"""The ``controlPlane`` module's data: the Gateway selection, primary and secondary."""

from typing import Literal

from pyvelo.core import ConfigModel

GatewayMode = Literal["dynamic", "static"]


class GatewaySelection(ConfigModel):
    primary: str
    secondary: str
    mode: GatewayMode


class ControlPlaneData(ConfigModel):
    gateway_selection: GatewaySelection
