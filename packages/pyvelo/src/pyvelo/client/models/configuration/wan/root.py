from typing import Annotated

from pydantic_core import MISSING

from pyvelo.core import VcoModel
from pyvelo.core.fields import EdgeLocal, KeyField

from .link import WanLink
from .network import WanNetwork


class WanData(VcoModel):
    networks: Annotated[list[WanNetwork] | MISSING, KeyField("name"), EdgeLocal] = (
        MISSING
    )
    links: Annotated[list[WanLink] | MISSING, KeyField("name"), EdgeLocal] = MISSING
