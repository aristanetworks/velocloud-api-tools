"""A ``deviceSettings:clientConnector:config`` ref may arrive with ``data: null``.

The Orchestrator returns it so for an edge whose client connector has no data
object, and an edge configuration that holds one must still load, be read back
as it came, and plan nothing when nothing is edited.
"""

import copy
from typing import Any

from pyvelo.client.models.configuration.device_settings.refs import (
    DeviceSettingsClientConnectorConfigRef,
    DeviceSettingsRefs,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResultModel
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.core import ConfigContext
from pyvelo.testing import make_module_json

REF_NAME = "deviceSettings:clientConnector:config"
CONNECTOR_REF: dict[str, Any] = {
    "id": 5,
    "enterpriseObjectId": 6,
    "configurationId": 10,
    "moduleId": 1,
    "segmentObjectId": None,
    "ref": REF_NAME,
    "object": "CLIENT_CONNECTOR",
    "name": "connector",
    "logicalId": "00000000-0000-0000-0000-000000000009",
    "status": None,
    "data": None,
}
DS_RAW: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [
        {
            "segment": {
                "segmentId": 0,
                "name": "Global Segment",
                "type": "REGULAR",
                "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
            }
        }
    ],
}


def test_a_client_connector_ref_with_null_data_validates():
    ref = DeviceSettingsClientConnectorConfigRef.model_validate(CONNECTOR_REF)
    assert ref.data is None
    refs = DeviceSettingsRefs.model_validate({REF_NAME: CONNECTOR_REF})
    assert refs.client_connector_config == ref


def test_a_client_connector_ref_with_data_still_validates():
    ref = DeviceSettingsClientConnectorConfigRef.model_validate(
        {**CONNECTOR_REF, "data": {"enabled": True}}
    )
    assert ref.data == {"enabled": True}


def test_an_edge_configuration_stack_with_such_a_ref_loads_and_keeps_it_as_read():
    row = make_module_json(
        "deviceSettings",
        copy.deepcopy(DS_RAW),
        module_id=1,
        configuration_id=10,
        refs={REF_NAME: CONNECTOR_REF},
    )
    edge = {"name": "Edge Specific Profile", "id": 10, "modules": [row]}
    profile = {
        "name": "P",
        "id": 20,
        "modules": [
            make_module_json(
                "deviceSettings",
                copy.deepcopy(DS_RAW),
                module_id=2,
                configuration_id=20,
            )
        ],
    }
    stack = GetEdgeConfigurationStackResultModel.model_validate([edge, profile]).root
    module = stack[0].device_settings
    assert isinstance(module, DeviceSettingsConfigurationModule)
    assert module.raw_refs == {REF_NAME: CONNECTOR_REF}
    plan = plan_module(module, level=ConfigContext.EDGE)
    assert (plan.operation, plan.refs) == ("none", None)
