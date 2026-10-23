import uuid
from typing import Annotated

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel

from ..common import AddressType
from .common import NetworkType, WanMode


class WanNetwork(VcoModel):
    mode: WanMode = "dynamic"
    name: str
    logical_id: str
    internal_id: uuid.UUID
    type_: Annotated[NetworkType, Alias("type")] = "ETHERNET"
    isp: str | None = None
    ip_address: str | None = None
    interface: str
    gateway: str | None = None
    cidr_prefix: int | None = None
    netmask: str | None = None
    private_ip_address: str | None = None
    addressing_version: AddressType = "IPv4"
    last_active: int | None = None

    # IPv6 fields
    ip_v6_address: str | None = None
    preferred_family: str | None = "IPv4"

    # Cellular/Wireless specific fields
    apn: str | None | MISSING = MISSING
    dial_number: str | None | MISSING = MISSING
    isp_key: str | None | MISSING = MISSING
    username: str | None | MISSING = MISSING
    password: str | None | MISSING = MISSING
