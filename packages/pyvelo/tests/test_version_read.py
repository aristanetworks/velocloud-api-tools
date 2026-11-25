"""A module write that sends refs reads the module's version first.

With the VCO's version precheck off, a stale write is refused for ``data`` but
still replaces the refs. So ``write_module`` makes one cheap request, the
module's ``version`` (``getConfigurationModules`` with ``noData``), before it
sends refs; a changed version is a conflict before anything is sent, unless the
module there already holds the write's changes.
"""

import copy
import pickle
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import ConfigConflictError, VcoOperationError
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.module_write import ModulePlan, plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleVersion,
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
    new_edge_module,
)
from pyvelo.core.identity import NetworkServiceIdentity
from pyvelo.core.layers import Edge
from pyvelo.testing import (
    ModuleStore,
    make_jsonrpc_response,
    make_module_json,
    store_of,
)
from refs_helpers import (
    CONFIG,
    DNS_A,
    DS_ID,
    PRIMARY,
    copy_of_data,
    set_primary_provider,
    store_with_services,
    table_registry,
    unset_provider_module,
)

from ._type_support import MockVcoClientFactory, parse_json_object
from .write_helpers import fw

VERSION = "configuration/getConfigurationModules"
UPDATE = "configuration/updateConfigurationModule"
GET = "configuration/getConfiguration"


async def provider_set(
    *, with_data_change: bool = False
) -> tuple[DeviceSettingsConfigurationModule, ModulePlan]:
    """Device settings that get a primary provider and its row, and beside it ``ntp`` if asked."""
    module = unset_provider_module()
    current = copy_of_data(module)
    set_primary_provider(current, uuid.UUID(DNS_A))
    if with_data_change:
        _ = current.merge_patch({"ntp": {"enabled": True}})
    resolvers = await table_registry(
        NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")
    )
    return module, plan_module(module, current, resolvers=resolvers)


def a_store_holding(
    module: DeviceSettingsConfigurationModule, **knobs: Any
) -> ModuleStore:
    """A store that holds *module* and the services and segments the plan's rows point at."""
    store = store_of(module, **knobs)
    store.objects.update(store_with_services().objects)
    return store


def methods(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests]


def elsewhere(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller did not edit."""
    row["data"]["future"] = 2


async def test_a_write_that_sends_refs_reads_the_version_first(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    write = await client.write_module(module, plan)
    assert methods(store) == [VERSION, UPDATE]
    method, params = store.requests[0]
    assert (method, params["configurationId"]) == (VERSION, CONFIG)
    assert params["noData"] is True and params["modules"] == ["deviceSettings"]
    assert store.requests[1][1]["version"] == "v1"
    assert write.module.version == store.rows[DS_ID]["version"]


async def test_the_version_read_is_not_a_read_of_the_data(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)
    assert GET not in methods(store)


async def test_a_changed_version_is_a_conflict_before_anything_is_sent(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    store.other_writer(DS_ID, elsewhere)
    refs_before = copy.deepcopy(store.rows[DS_ID].get("refs"))
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert methods(store) == [VERSION, GET]
    assert store.rows[DS_ID].get("refs") == refs_before
    assert (error.stated, error.sent) == (False, False)
    assert error.module is not None
    assert error.module.version == store.rows[DS_ID]["version"]
    assert error.module.refs_loaded
    assert error.detail == (
        "the module's version on the VCO is not the one read, and the module "
        "there does not hold this write's changes; nothing was sent: redo the "
        "lost edits on error.module and save again"
    )
    assert sorted(str(c.path) for c in error.lost_edits) == [
        f'"{PRIMARY}"[{DNS_A},501]',
        "ntp",
    ]
    assert [str(c.path) for c in error.server_changes] == ["future"]
    assert error.version == "v1"


async def test_the_headline_says_nothing_was_sent_and_not_that_the_vco_refused(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    message = str(caught.value)
    assert "changed on the VCO since it was read" in message
    assert "nothing was sent" in message
    assert "seems to have refused" not in message
    assert "without saying so" not in message


async def test_a_conflict_before_sending_is_the_same_with_the_vcos_check_on(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module, conditional_check=True)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    assert (caught.value.stated, caught.value.sent) == (False, False)
    assert UPDATE not in methods(store)


async def test_a_conflict_before_sending_survives_pickle_and_copy(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    for copied in (
        copy.copy(caught.value),
        copy.deepcopy(caught.value),
        pickle.loads(pickle.dumps(caught.value)),
    ):
        assert (copied.stated, copied.sent) == (False, False)
        assert str(copied) == str(caught.value)


async def test_a_retry_whose_changes_are_stored_passes_without_sending(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    first = await client.write_module(module, plan)
    store.requests.clear()
    second = await client.write_module(module, plan)
    assert methods(store) == [VERSION, GET]
    assert second.module.version == first.module.version
    assert second.module.version == store.rows[DS_ID]["version"]
    assert second.operation == "update"
    assert sorted(str(c.path) for c in second.applied) == [
        f'"{PRIMARY}"[{DNS_A},501]',
        "ntp",
    ]


async def test_a_retry_after_a_lost_answer_passes_without_a_second_update(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    store.lose_answers.add(1)
    client = await mock_vco_client(store, max_retries=0)
    with pytest.raises(httpx.ReadTimeout):
        _ = await client.write_module(module, plan)
    store.requests.clear()
    write = await client.write_module(module, plan)
    assert UPDATE not in methods(store)
    assert write.module.version == store.rows[DS_ID]["version"]


async def test_a_write_that_sends_no_refs_has_no_version_read(
    mock_vco_client: MockVcoClientFactory,
):
    module = fw({"segments": [], "firewall_enabled": True}, layer=Edge)
    module.data.firewall_enabled.set_local(False)
    store = store_of(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan_module(module))
    assert methods(store) == [UPDATE]


async def test_a_device_settings_edit_with_no_segments_has_no_version_read(
    mock_vco_client: MockVcoClientFactory,
):
    module = DeviceSettingsConfigurationModule.model_validate(
        make_module_json(
            "deviceSettings",
            {"lan": {"networks": [], "interfaces": []}, "segments": []},
            configuration_id=CONFIG,
            module_id=DS_ID,
        )
    )
    current = copy_of_data(module)
    _ = current.merge_patch({"ntp": {"enabled": True}})
    plan = plan_module(module, current)
    assert plan.refs is None
    store = store_of(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)
    assert methods(store) == [UPDATE]


async def test_an_insert_has_no_version_read(mock_vco_client: MockVcoClientFactory):
    module = new_edge_module(ConfigurationModuleName.QOS, 10)
    assert isinstance(module, QosConfigurationModule)
    module.data.segments.append(
        QosSegment[Edge].model_validate(
            {
                "segment": {
                    "segmentId": 0,
                    "name": "Global Segment",
                    "type": "REGULAR",
                    "segmentLogicalId": "00000000-0000-0000-0000-000000000000",
                }
            }
        )
    )
    plan = plan_module(module)
    assert plan.operation == "insert"
    store = store_of(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)
    assert VERSION not in methods(store)


async def test_a_module_with_no_version_stays_unconditional(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    module = module.model_copy(update={"version": None})
    assert plan.refs is not None
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)
    assert VERSION not in methods(store)


async def test_a_module_that_is_gone_is_a_conflict_with_no_module(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    del store.rows[DS_ID]
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(module, plan)
    error = caught.value
    assert (error.stated, error.sent) == (False, False)
    assert error.module is None
    assert error.lost_edits == ()
    assert UPDATE not in methods(store)
    message = str(error)
    assert "no longer holds" in message
    assert "read the configuration again" in message
    assert "seems to have refused" not in message


def failing(
    store: ModuleStore, method: str
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that answers *method* with an error and sends the rest to *store*."""

    def handler(request: httpx.Request) -> httpx.Response:
        if parse_json_object(request.content)["method"] != method:
            return store(request)
        store.requests.append((method, {}))
        return httpx.Response(
            200,
            json=make_jsonrpc_response(error={"code": -32000, "message": "denied"}),
        )

    return handler


async def test_an_error_of_the_version_read_propagates_and_nothing_is_sent(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    client = await mock_vco_client(failing(store, VERSION))
    with pytest.raises(VcoOperationError):
        _ = await client.write_module(module, plan)
    assert methods(store) == [VERSION]


async def test_an_error_of_the_heavier_read_propagates_and_nothing_is_sent(
    mock_vco_client: MockVcoClientFactory,
):
    module, plan = await provider_set()
    store = a_store_holding(module)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(failing(store, GET))
    with pytest.raises(VcoOperationError):
        _ = await client.write_module(module, plan)
    assert methods(store) == [VERSION, GET]


def test_a_version_row_has_no_data():
    row = ConfigurationModuleVersion.model_validate(
        {"id": 10, "name": "deviceSettings", "version": "5", "configurationId": 1}
    )
    assert (row.id_, row.name, row.version) == (10, "deviceSettings", "5")
    empty = ConfigurationModuleVersion.model_validate(
        {"id": 10, "name": "QOS", "version": None}
    )
    assert empty.version is None


async def test_the_version_row_of_another_module_of_the_name_is_not_this_one(
    mock_vco_client: MockVcoClientFactory,
):
    """Of several rows of a name, the version compared is that of the module's own ID."""
    module, plan = await provider_set()
    store = a_store_holding(module)
    other = copy.deepcopy(store.rows[DS_ID])
    other["id"] = DS_ID + 100
    other["version"] = "other"
    store.rows[other["id"]] = other
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan)
    assert methods(store) == [VERSION, UPDATE]
