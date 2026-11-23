"""An insert carries its derived refs in the one request, and every insert is read back.

An insert's ``returnData`` row does not promise refs, so ``write_module`` reads
the module back from its configuration, with its refs, and checks the module
against that read.

The module is a QOS module planned from a test-local ``QosData`` with two
owners: ``probe`` at the module level and one in each segment.
"""

import uuid
from typing import Annotated, Any, Literal

import pytest
from pydantic import Field
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import RefsDroppedError
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    QosConfigurationModule,
    new_edge_module,
)
from pyvelo.core import OwnsRef
from pyvelo.core.fields import KeyField
from pyvelo.core.identity import NetworkServiceIdentity, SegmentIdentity
from pyvelo.testing import ModuleStore, store_of
from refs_helpers import (
    CONFIG,
    DNS_A,
    DNS_B,
    SEG0,
    SEG0_OBJECT,
    store_with_services,
    table_registry,
)

from ._type_support import MockVcoClientFactory

INSERT = "configuration/insertConfigurationModule"
GET = "configuration/getConfiguration"
PROBE = "test:probe"
SEGMENT_PROBE = "test:segment-probe"

DNS_A_IDENTITY = NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
DNS_B_IDENTITY = NetworkServiceIdentity("dns-b", uuid.UUID(DNS_B), 702, "dns")
SEG0_IDENTITY = SegmentIdentity("Global Segment", 0, SEG0_OBJECT, uuid.UUID(SEG0))


class ProbeSegment(QosSegment):
    probe: Annotated[
        uuid.UUID | Literal[""],
        OwnsRef(
            SEGMENT_PROBE,
            NetworkServiceIdentity,
            by="logical_id",
            row="logicalId",
            per_segment=True,
        ),
    ] = ""


class ProbeData(QosData):
    probe: Annotated[
        uuid.UUID | Literal[""],
        OwnsRef(PROBE, NetworkServiceIdentity, by="logical_id", row="logicalId"),
    ] = ""
    # Redeclared with the subclassed item, so that the owner in it is found.
    segments: Annotated[  # pyright: ignore[reportIncompatibleVariableOverride]
        list[ProbeSegment], KeyField("segment.name", alt="segment.segment_id")
    ] = Field(default_factory=list)


def global_segment(**values: Any) -> dict[str, Any]:
    return {
        "segment": {
            "segmentId": 0,
            "name": "Global Segment",
            "type": "REGULAR",
            "segmentLogicalId": SEG0,
        },
        **values,
    }


def a_store() -> ModuleStore:
    """A store with the services and segments, and the configuration the module goes in."""
    store = store_with_services()
    store.add_configuration({"id": CONFIG, "name": "Edge Specific Profile"})
    return store


async def probe_insert(
    *, probe: str | None = DNS_A, segment_probe: str = DNS_B
) -> tuple[QosConfigurationModule, ModulePlan]:
    """A new QOS module and its plan, with both owners set unless *probe* is None."""
    module = new_edge_module(ConfigurationModuleName.QOS, CONFIG)
    assert isinstance(module, QosConfigurationModule)
    data = ProbeData.model_validate(
        {
            **({"probe": probe} if probe is not None else {}),
            "segments": [global_segment(probe=segment_probe)],
        }
    )
    registry = await table_registry(DNS_A_IDENTITY, DNS_B_IDENTITY, SEG0_IDENTITY)
    plan = plan_module(module, data, resolvers=registry)
    assert plan.operation == "insert"
    return module, plan


def methods(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests]


async def test_an_insert_with_a_declared_identity_sends_its_rows_in_one_request(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert()
    store = a_store()
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)

    (insert,) = [params for method, params in store.requests if method == INSERT]
    assert insert["refs"][PROBE] == [
        {"configurationId": CONFIG, "enterpriseObjectId": 701, "ref": PROBE}
    ]
    (row,) = insert["refs"][SEGMENT_PROBE]
    assert set(row) == {
        "configurationId",
        "enterpriseObjectId",
        "ref",
        "segmentObjectId",
    }
    assert row["enterpriseObjectId"] == 702
    assert row["segmentObjectId"] == SEG0_OBJECT


async def test_an_insert_is_read_back_and_its_rows_checked(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert()
    store = a_store()
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)

    assert methods(store) == [INSERT, GET]
    assert write.module.refs_loaded
    assert isinstance(write.module, QosConfigurationModule)
    (inserted,) = store.rows
    assert write.module.id_ == inserted
    assert write.module.version == store.rows[inserted]["version"]
    assert write.module.raw_refs == store.rows[inserted]["refs"]
    stored = store.rows[inserted]["refs"]
    assert stored[PROBE]["enterpriseObjectId"] == 701
    assert stored[PROBE]["moduleId"] == inserted
    assert stored[SEGMENT_PROBE]["enterpriseObjectId"] == 702


async def test_an_insert_the_vco_counted_is_read_back_once(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert()
    store = a_store()
    store.return_data = False
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)

    assert methods(store) == [INSERT, GET]
    assert write.module.refs_loaded
    assert write.module.id_ == next(iter(store.rows))


async def test_an_inserted_row_the_vco_skipped_is_refs_dropped(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert()
    store = a_store()
    store.skipped_objects.add(701)
    client = await mock_vco_client(store)

    with pytest.raises(RefsDroppedError) as caught:
        _ = await client.write_module(module, plan)

    assert methods(store) == [INSERT, GET]
    assert [edit.part for edit in caught.value.lost_edits] == ["refs"]


async def test_a_new_qos_module_with_reconciled_segments_is_inserted_without_refs(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert(probe=None, segment_probe="")
    assert plan.refs is None
    store = a_store()
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)

    (insert,) = [params for method, params in store.requests if method == INSERT]
    assert "refs" not in insert
    assert methods(store) == [INSERT, GET]
    assert write.module.refs_loaded


async def test_a_module_found_by_if_absent_is_not_read_again(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await probe_insert()
    first = a_store()
    client = await mock_vco_client(first)
    _ = await client.write_module(module, plan)

    again = store_of(module)
    again.objects.update(first.objects)
    again.rows.update(first.rows)
    again.configurations.update(first.configurations)
    client = await mock_vco_client(again)
    write = await client.write_module(module, plan, if_absent=True)

    assert methods(again) == [GET]
    assert write.module.refs_loaded
