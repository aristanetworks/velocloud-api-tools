import uuid
from typing import Annotated

from pyvelo.core import Alias, Tinyint, VcoModel, VcoTimestamp


class Gateway(VcoModel):
    id_: Annotated[int | None, Alias("id")] = None
    created: VcoTimestamp | None = None
    name: str | None = None
    logical_id: uuid.UUID | None = None
    activation_key: str | None = None
    activation_state: str | None = None
    activation_time: VcoTimestamp | None = None
    alerts_enabled: Tinyint | None = None
    build_number: str | None = None
    description: str | None = None
    device_id: str | None = None
    dns_name: str | None = None
    network_id: int | None = None
    site_id: int | None = None
