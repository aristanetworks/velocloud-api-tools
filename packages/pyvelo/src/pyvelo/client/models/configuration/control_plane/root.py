from typing import Literal

from pyvelo.core import VcoModel

GatewayMode = Literal["dynamic", "static"]


class GatewaySelection(VcoModel):
    primary: str
    secondary: str
    mode: GatewayMode


class ControlPlaneData(VcoModel):
    gateway_selection: GatewaySelection
