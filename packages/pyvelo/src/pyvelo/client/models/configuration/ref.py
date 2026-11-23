import uuid
from typing import Annotated

from pydantic import MISSING, ConfigDict

from pyvelo.core import Alias, VcoModel


class ConfigurationRef(VcoModel):
    model_config = ConfigDict(frozen=True)

    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    configuration_id: int
    module_id: int
    logical_id: uuid.UUID
    enterprise_object_id: int
    segment_logical_id: uuid.UUID | None | MISSING = MISSING
    segment_object_id: int | None | MISSING = MISSING
    name: str | MISSING = MISSING
