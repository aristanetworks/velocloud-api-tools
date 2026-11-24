"""Builders for the module write tests."""

import copy
from typing import Any, overload

from pydantic import MISSING
from pyvelo.client.schemas.configuration import (
    FirewallConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing import make_module_json


@overload
def fw(
    raw: dict[str, Any],
    *,
    layer: type[Edge],
    module_id: int = ...,
    configuration_id: int = ...,
    version: str | None = ...,
    refs: object = ...,
) -> FirewallConfigurationModule[Edge]: ...
@overload
def fw(
    raw: dict[str, Any],
    *,
    layer: type[Profile],
    module_id: int = ...,
    configuration_id: int = ...,
    version: str | None = ...,
    refs: object = ...,
) -> FirewallConfigurationModule[Profile]: ...
def fw(
    raw: dict[str, Any],
    *,
    layer: type[Edge] | type[Profile],
    module_id: int = 1,
    configuration_id: int = 10,
    version: str | None = "v1",
    refs: object = MISSING,
) -> FirewallConfigurationModule[Edge] | FirewallConfigurationModule[Profile]:
    """A firewall module of the layer *layer*, read with *raw* as its data."""
    row = make_module_json(
        "firewall",
        copy.deepcopy(raw),
        module_id=module_id,
        configuration_id=configuration_id,
        version=version,
        refs=copy.deepcopy(refs),
    )
    if layer is Edge:
        return FirewallConfigurationModule[Edge].model_validate(row)
    return FirewallConfigurationModule[Profile].model_validate(row)


@overload
def qos_mod(
    raw: dict[str, Any],
    *,
    layer: type[Edge],
    module_id: int = ...,
    configuration_id: int = ...,
    version: str | None = ...,
) -> QosConfigurationModule[Edge]: ...
@overload
def qos_mod(
    raw: dict[str, Any],
    *,
    layer: type[Profile],
    module_id: int = ...,
    configuration_id: int = ...,
    version: str | None = ...,
) -> QosConfigurationModule[Profile]: ...
def qos_mod(
    raw: dict[str, Any],
    *,
    layer: type[Edge] | type[Profile],
    module_id: int = 1,
    configuration_id: int = 10,
    version: str | None = "v1",
) -> QosConfigurationModule[Edge] | QosConfigurationModule[Profile]:
    """A QoS module of the layer *layer*, read with *raw* as its data."""
    row = make_module_json(
        "QOS",
        copy.deepcopy(raw),
        module_id=module_id,
        configuration_id=configuration_id,
        version=version,
        refs={},
    )
    if layer is Edge:
        return QosConfigurationModule[Edge].model_validate(row)
    return QosConfigurationModule[Profile].model_validate(row)
