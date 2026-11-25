"""``check_module_version``: the version read of a write that sends refs, made on its own.

A save that sends role steps before it writes device settings checks the
module's version first, so that a module another writer changed stops the save
before any step is sent. ``write_module(..., version_checked=True)`` then does
not read the version again.
"""

import pytest
from pyvelo.client.exceptions import ConfigConflictError
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.testing import ModuleStore, make_module_json, store_of
from refs_helpers import CONFIG, DS_ID, copy_of_data

from ._type_support import MockVcoClientFactory
from .test_version_read import (
    GET,
    UPDATE,
    VERSION,
    a_store_holding,
    elsewhere,
    methods,
    provider_set,
)


async def test_a_version_that_is_the_one_read_answers_none_after_one_request(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set()
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    assert await client.check_module_version(module, plan) is None
    assert methods(store) == [VERSION]
    assert store.requests[0][1]["configurationId"] == CONFIG


async def test_a_changed_version_with_the_changes_already_stored_answers_that_write(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    first = await client.write_module(module, plan)
    store.requests.clear()
    answer = await client.check_module_version(module, plan)
    assert answer is not None
    assert methods(store) == [VERSION, GET]
    assert answer.operation == "update"
    assert answer.module.version == first.module.version
    assert answer.module.version == store.rows[DS_ID]["version"]


async def test_a_changed_version_without_the_changes_is_a_conflict_and_sends_nothing(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.check_module_version(module, plan)
    assert (caught.value.stated, caught.value.sent) == (False, False)
    assert caught.value.module is not None
    assert caught.value.module.version == store.rows[DS_ID]["version"]
    assert methods(store) == [VERSION, GET]


async def test_a_module_without_a_version_is_not_read(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set()
    module = module.model_copy(update={"version": None})
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    assert await client.check_module_version(module, plan) is None
    assert store.requests == []


async def test_a_module_without_a_configuration_is_not_read(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set()
    module = module.model_copy(update={"configuration_id": None})
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    assert await client.check_module_version(module, plan) is None
    assert store.requests == []


async def test_a_plan_that_sends_no_refs_is_not_read(
    mock_vco_client: MockVcoClientFactory,
) -> None:
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
    assert await client.check_module_version(module, plan) is None
    assert store.requests == []


async def test_a_plan_for_another_module_is_refused_before_a_request(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set()
    other = DeviceSettingsConfigurationModule.model_validate(
        make_module_json(
            "deviceSettings",
            {"lan": {"networks": [], "interfaces": []}, "segments": []},
            configuration_id=CONFIG,
            module_id=DS_ID,
        )
    ).model_copy(update={"name": "QOS"})
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    with pytest.raises(ValueError, match="the plan is for deviceSettings, not QOS"):
        _ = await client.check_module_version(other, plan)
    assert store.requests == []


async def test_version_checked_sends_no_version_read(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set()
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan, version_checked=True)
    assert methods(store) == [UPDATE]


async def test_version_checked_leaves_the_conditional_update_to_refuse_a_stale_write(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set(with_data_change=True)
    store: ModuleStore = a_store_holding(module, conditional_check=True)
    store.other_writer(DS_ID, elsewhere)
    client = await mock_vco_client(store)
    with pytest.raises(ConfigConflictError):
        _ = await client.write_module(module, plan, version_checked=True)
    assert VERSION not in methods(store)
    assert UPDATE in methods(store)


async def test_version_checked_does_not_skip_the_read_that_decides_a_rebase(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module, plan = await provider_set(with_data_change=True)
    store = a_store_holding(module)
    client = await mock_vco_client(store)
    _ = await client.write_module(module, plan, rebase=True, version_checked=True)
    assert methods(store)[0] == VERSION
