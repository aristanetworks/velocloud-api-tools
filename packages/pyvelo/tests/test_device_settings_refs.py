"""A ``deviceSettings:clientConnector:config`` ref may arrive with ``data: null``.

The Orchestrator returns it so for an edge whose client connector has no data
object, and an edge configuration that holds one must still load, be read back
as it came, and plan nothing when nothing is edited.
"""

import copy
import pickle
import uuid
from typing import Any

import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client.models.configuration.device_settings.refs import (
    DeviceSettingsClientConnectorConfigRef,
    DeviceSettingsRefs,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResultModel
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.core import ConfigContext, Edge
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


def test_a_client_connector_ref_with_null_data_validates() -> None:
    ref = DeviceSettingsClientConnectorConfigRef.model_validate(CONNECTOR_REF)
    assert ref.data is None
    refs = DeviceSettingsRefs.model_validate({REF_NAME: CONNECTOR_REF})
    assert refs.client_connector_config == ref


def test_a_client_connector_ref_with_data_still_validates() -> None:
    ref = DeviceSettingsClientConnectorConfigRef.model_validate(
        {**CONNECTOR_REF, "data": {"enabled": True}}
    )
    assert ref.data == {"enabled": True}


def test_an_edge_configuration_stack_with_such_a_ref_loads_and_keeps_it_as_read() -> (
    None
):
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


SEGMENT_REF_NAME = "deviceSettings:segment"


def _segment_ref(logical_id: str, enterprise_object_id: int) -> dict[str, Any]:
    return {
        "id": enterprise_object_id,
        "enterpriseObjectId": enterprise_object_id,
        "configurationId": 10,
        "moduleId": 1,
        "segmentObjectId": None,
        "ref": SEGMENT_REF_NAME,
        "object": "NETWORK_SEGMENT",
        "name": "segment",
        "logicalId": logical_id,
        "type": "REGULAR",
        "data": {"segmentId": 0},
    }


ROW_A = _segment_ref("00000000-0000-0000-0000-00000000000a", 1)
ROW_B = _segment_ref("00000000-0000-0000-0000-00000000000b", 2)


def _module(refs: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    return DeviceSettingsConfigurationModule.model_validate(
        make_module_json("deviceSettings", copy.deepcopy(DS_RAW), refs=refs)
    )


def test_assigning_to_a_ref_row_raises() -> None:
    refs = DeviceSettingsRefs.model_validate({REF_NAME: CONNECTOR_REF})
    row = refs.client_connector_config
    assert isinstance(row, DeviceSettingsClientConnectorConfigRef)
    with pytest.raises(ValidationError, match="frozen"):
        row.logical_id = uuid.UUID(int=1)
    with pytest.raises(ValidationError, match="frozen"):
        row.status = "x"


def test_assigning_to_the_refs_container_raises() -> None:
    refs = DeviceSettingsRefs.model_validate({REF_NAME: CONNECTOR_REF})
    with pytest.raises(ValidationError, match="frozen"):
        refs.client_connector_config = MISSING


def test_a_list_of_rows_is_a_tuple() -> None:
    module = _module({SEGMENT_REF_NAME: [ROW_A, ROW_B]})
    assert isinstance(module.refs, DeviceSettingsRefs)
    rows = module.refs.segment
    assert isinstance(rows, tuple)
    assert [row.enterprise_object_id for row in rows] == [1, 2]
    with pytest.raises(AttributeError):
        rows.append(rows[0])  # pyright: ignore[reportAttributeAccessIssue]


def test_a_single_row_stays_a_row() -> None:
    module = _module({SEGMENT_REF_NAME: ROW_A})
    assert isinstance(module.refs, DeviceSettingsRefs)
    assert not isinstance(module.refs.segment, tuple)


@pytest.mark.parametrize("segment_type", ["REGULAR", "CDE", "PRIVATE"])
def test_a_segment_row_of_each_type_loads_and_dumps_as_read(segment_type: str) -> None:
    row = {**ROW_A, "type": segment_type}
    module = _module({SEGMENT_REF_NAME: row})
    assert isinstance(module.refs, DeviceSettingsRefs)
    dumped_type = module.refs.model_dump(by_alias=True)[SEGMENT_REF_NAME]["type"]
    assert dumped_type == segment_type
    assert type(dumped_type) is str  # the wire string, not a StrEnum member


def test_assigning_module_refs_raises_for_device_settings() -> None:
    module = _module({SEGMENT_REF_NAME: ROW_A})
    with pytest.raises(ValidationError, match="frozen"):
        module.refs = MISSING
    with pytest.raises(ValidationError, match="frozen"):
        module.refs = DeviceSettingsRefs()


def test_assigning_module_refs_raises_for_qos() -> None:
    module = QosConfigurationModule[Edge].model_validate(
        make_module_json("QOS", {"segments": []}, refs={"qos:ref": {"a": 1}})
    )
    assert module.refs == {"qos:ref": {"a": 1}}
    with pytest.raises(ValidationError, match="frozen"):
        module.refs = {}


@pytest.mark.parametrize("held", [ROW_A, [ROW_A, ROW_B], [ROW_A]])
def test_a_frozen_refs_dump_is_the_json_read(held: Any) -> None:
    refs = DeviceSettingsRefs.model_validate({SEGMENT_REF_NAME: copy.deepcopy(held)})
    dumped = refs.model_dump(mode="json", exclude_unset=True)
    assert dumped == {SEGMENT_REF_NAME: held}
    assert isinstance(dumped[SEGMENT_REF_NAME], list) == isinstance(held, list)


def test_a_module_holding_frozen_refs_is_marked_detached_and_copies_and_pickles() -> (
    None
):
    module = _module({SEGMENT_REF_NAME: [ROW_A, ROW_B]})
    module.mark_detached()
    assert module.is_detached
    assert pickle.loads(pickle.dumps(module)).raw_refs == module.raw_refs
    assert copy.deepcopy(module).raw_refs == module.raw_refs
    assert not copy.deepcopy(module).is_detached
