from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.client.enums.profiles import (
    GetConfigurationWith,
    GetEnterpriseConfigurationsWith,
)
from pyvelo.core import Alias, NoneIsMissing, VcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import ProfileIdentity


class GetEnterpriseConfigurations(VcoModel):
    enterprise_id: int | MISSING = MISSING
    with_: Annotated[list[GetEnterpriseConfigurationsWith] | MISSING, Alias("with")] = (
        MISSING
    )


class CreateProfile(VcoModel):
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = Field(default=MISSING)
    name: str
    description: str | MISSING = Field(default=MISSING)


class CreateProfileResult(VcoModel):
    id_: Annotated[int, Alias("id")]


class GetConfiguration(VcoModel):
    enterprise_id: int | MISSING = MISSING
    id_: Annotated[int, Alias("id"), Resolvable(ProfileIdentity, "id")]
    with_: Annotated[list[GetConfigurationWith] | MISSING, Alias("with")] = MISSING
