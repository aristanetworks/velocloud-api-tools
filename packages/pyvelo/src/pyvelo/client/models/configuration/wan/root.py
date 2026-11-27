"""The ``WAN`` module's data: the Edge's networks and links."""

from typing import Annotated

from pydantic import MISSING

from pyvelo.core import ConfigModel
from pyvelo.core.fields import EdgeLocal, KeyField

from .link import WanLink
from .network import WanNetwork


class WanData(ConfigModel):
    networks: Annotated[
        list[WanNetwork] | MISSING,
        KeyField("internal_id", "addressing_version", label="name"),
        EdgeLocal,
    ] = MISSING
    links: Annotated[
        list[WanLink] | MISSING,
        KeyField("internal_id", "addressing_version", label="name"),
        EdgeLocal,
    ] = MISSING
