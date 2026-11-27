"""A ref row: the module, enterprise object and segment a module's ref names."""

import uuid
from typing import Annotated, ClassVar

from pydantic import MISSING, ConfigDict

from pyvelo.core import Alias, ConfigModel


class ConfigurationRef(ConfigModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    configuration_id: int
    module_id: int
    logical_id: uuid.UUID
    enterprise_object_id: int
    segment_logical_id: uuid.UUID | None | MISSING = MISSING
    segment_object_id: int | None | MISSING = MISSING
    name: str | MISSING = MISSING
