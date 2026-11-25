"""A module written after a profile device settings write is rebased onto what that write changed.

A profile device settings write can change other modules of the profile (it
rewrites QoS rule flags, and it bumps the versions of every module when a
segment becomes in use). So ``write_module(..., rebase=True)`` reads each
module's version first. A changed version makes it read the module in full and
apply the caller's edits to what is there, and it raises a conflict only where
the other change reached a path the caller also edits.

The module is a profile QoS module with a segment, so that it sends its refs.
The device settings write is modelled by an ``on_store`` hook that changes the
QoS row as another writer would.
"""

import copy
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from pyvelo.client.exceptions import ConfigConflictError
from pyvelo.client.models.configuration import QosData
from pyvelo.client.models.configuration.profile_segments import default_rules
from pyvelo.client.models.configuration.qos.root import ServiceRateLimit
from pyvelo.client.models.configuration.qos.segment import WebProxy
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan, plan_module, rebase_onto
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.core import Profile
from pyvelo.testing import ModuleStore, make_module_json, store_of
from refs_helpers import (
    CONFIG,
    QOS_ID,
    SEG0,
    copy_of_data,
    store_with_services,
    unset_provider_module,
)

from ._type_support import MockVcoClientFactory

VERSION = "configuration/getConfigurationModules"
UPDATE = "configuration/updateConfigurationModule"
GET = "configuration/getConfiguration"
GLOBAL_SEGMENT: dict[str, Any] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": SEG0,
}
GUEST_SEGMENT: dict[str, Any] = {
    "segmentId": 1,
    "name": "Guest",
    "type": "REGULAR",
    "segmentLogicalId": "22222222-2222-4222-8222-222222222222",
}
RATE: dict[str, Any] = {"enabled": False, "inputType": "percent", "value": 1}


def qos_module(
    *, version: str = "q1", enabled: bool = False, with_defaults: bool = True
) -> QosConfigurationModule[Profile]:
    """A profile QoS module with the Global Segment, a web proxy and a rate limit.

    The segment has the four ``defaults`` unless *with_defaults* is false.
    """
    data: dict[str, Any] = {
        "segments": [
            {
                "segment": GLOBAL_SEGMENT,
                "rules": [],
                "webProxy": {"providers": []},
                **({"defaults": default_rules()} if with_defaults else {}),
            }
        ],
        "serviceRateLimit": {**RATE, "enabled": enabled},
    }
    row = make_module_json(
        "QOS", data, module_id=QOS_ID, configuration_id=CONFIG, version=version
    )
    return QosConfigurationModule[Profile].model_validate(row)


def rate_set(module: QosConfigurationModule[Profile], value: int) -> ModulePlan:
    """The plan of *module* with its rate limit's value set to *value*."""
    data = module.data.model_copy(deep=True)
    rate = data.service_rate_limit
    assert isinstance(rate, ServiceRateLimit)
    rate.value = value
    return plan_module(module, data)


def providers_set(module: QosConfigurationModule[Profile]) -> ModulePlan:
    """The plan of *module* with a provider in the Global Segment's web proxy."""
    data = module.data.model_copy(deep=True)
    proxy = data.segments[0].web_proxy
    assert isinstance(proxy, WebProxy)
    proxy.providers.append("proxy-a")
    return plan_module(module, data)


def device_settings() -> tuple[DeviceSettingsConfigurationModule, ModulePlan]:
    """A profile device settings module and the plan of one edit to it."""
    module = unset_provider_module()
    current = copy_of_data(module)
    _ = current.merge_patch({"ntp": {"enabled": True}})
    return module, plan_module(module, current)


def a_store(
    ds: DeviceSettingsConfigurationModule,
    qos: QosConfigurationModule[Profile],
    propagation: Callable[[dict[str, Any]], None] | None,
) -> ModuleStore:
    """A store holding both modules; a write of device settings has *propagation* change the QoS row."""
    store = store_of(ds, qos)
    store.objects.update(store_with_services().objects)

    def hook(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings" and propagation is not None:
            store.other_writer(QOS_ID, propagation)

    store.on_store.append(hook)
    return store


def enabled(row: dict[str, Any]) -> None:
    """The propagation: a change to a key the caller does not edit."""
    row["data"]["serviceRateLimit"]["enabled"] = True


def rate_to(value: int) -> Callable[[dict[str, Any]], None]:
    def edit(row: dict[str, Any]) -> None:
        row["data"]["serviceRateLimit"]["value"] = value

    return edit


def a_guest_segment(row: dict[str, Any]) -> None:
    row["data"]["segments"].append({"segment": GUEST_SEGMENT, "rules": []})


def methods(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests]


async def device_settings_written(
    mock_vco_client: MockVcoClientFactory,
    store: ModuleStore,
    ds: DeviceSettingsConfigurationModule,
    plan: ModulePlan,
):
    """A client over *store*, after the write of *ds*; the requests are cleared."""
    client = await mock_vco_client(store)
    _ = await client.write_module(ds, plan)
    store.requests.clear()
    return client


async def test_a_changed_module_is_rebased_and_written_with_the_fresh_version(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, enabled)
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)
    fresh_version = store.rows[QOS_ID]["version"]
    assert fresh_version != "q1"

    write = await client.write_module(qos, rate_set(qos, 5), rebase=True)

    assert methods(store) == [VERSION, GET, UPDATE]
    (_, sent) = store.requests[-1]
    assert sent["version"] == fresh_version
    assert sent["_update"]["data"]["serviceRateLimit"] == {
        **RATE,
        "enabled": True,
        "value": 5,
    }
    assert store.rows[QOS_ID]["data"]["serviceRateLimit"] == {
        **RATE,
        "enabled": True,
        "value": 5,
    }
    # The write carries the plan it sent, and what the other writer changed, as merged.
    assert [str(c.path) for c in write.plan.changes] == ["serviceRateLimit.value"]
    assert [str(c.path) for c in write.applied] == ["serviceRateLimit.value"]
    assert [
        (str(c.path), c.as_read, c.stored, c.merged) for c in write.server_changes
    ] == [("serviceRateLimit.enabled", False, True, True)]
    assert write.module.version == store.rows[QOS_ID]["version"]
    assert write.module.version not in ("q1", fresh_version)


async def test_a_clash_raises_a_conflict_and_sends_nothing(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, rate_to(7))
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)
    held = copy.deepcopy(store.rows[QOS_ID])

    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(qos, rate_set(qos, 5), rebase=True)

    error = caught.value
    assert methods(store) == [VERSION, GET]
    assert store.rows[QOS_ID] == held
    assert (error.stated, error.sent) == (False, False)
    assert error.detail == (
        "the module changed on the VCO after this save wrote device settings, at a "
        "path this save also changes; nothing was sent"
    )
    assert error.module is not None
    assert error.module.version == held["version"]
    assert error.module.refs_loaded
    assert [
        (str(c.path), c.as_read, c.intended, c.stored, c.clash)
        for c in error.lost_edits
    ] == [("serviceRateLimit.value", 1, 5, 7, True)]
    assert error.server_changes == ()


async def test_an_unchanged_version_writes_as_planned(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, None)
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)

    write = await client.write_module(qos, rate_set(qos, 5), rebase=True)

    assert methods(store) == [VERSION, UPDATE]
    assert store.requests[-1][1]["version"] == "q1"
    assert write.server_changes == ()
    assert [str(c.path) for c in write.applied] == ["serviceRateLimit.value"]


async def test_without_rebase_a_changed_version_of_a_refs_write_is_a_conflict(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, enabled)
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)
    plan = rate_set(qos, 5)
    assert plan.refs is not None

    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(qos, plan)

    assert methods(store) == [VERSION, GET]
    assert (caught.value.stated, caught.value.sent) == (False, False)
    assert "the module there does not hold this write's changes" in caught.value.detail


async def test_a_write_that_sends_no_refs_is_not_read_without_rebase(
    mock_vco_client: MockVcoClientFactory,
):
    qos = qos_module()
    store = store_of(qos)
    client = await mock_vco_client(store)
    plan = rate_set(qos, 5)
    plan = replace(plan, refs=None)

    _ = await client.write_module(qos, plan)

    assert methods(store) == [UPDATE]


async def test_a_change_the_other_writer_made_too_is_written_as_nothing(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, rate_to(5))
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)
    held = copy.deepcopy(store.rows[QOS_ID])

    write: ModuleWrite[QosConfigurationModule[Profile]] = await client.write_module(
        qos, rate_set(qos, 5), rebase=True
    )

    assert methods(store) == [VERSION, GET]
    assert store.rows[QOS_ID] == held
    assert (write.operation, write.plan.operation) == ("none", "none")
    assert write.module.version == held["version"]
    rate = write.module.data.service_rate_limit
    assert isinstance(rate, ServiceRateLimit)
    assert (rate.enabled, rate.value) == (False, 5)
    assert write.server_changes == ()


async def test_a_module_gone_from_its_configuration_is_a_conflict_without_a_module(
    mock_vco_client: MockVcoClientFactory,
):
    qos = qos_module()
    store = store_of(qos)
    store.other_writer(QOS_ID, enabled)
    client = await mock_vco_client(store)
    plan = rate_set(qos, 5)
    # The version read finds the row, the configuration read does not.
    original_get = store.handle

    def handle(method: str, params: dict[str, Any]) -> object:
        if method == GET:
            store.rows.pop(QOS_ID)
        return original_get(method, params)

    store.handle = handle  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(qos, plan, rebase=True)

    assert caught.value.module is None
    assert (caught.value.stated, caught.value.sent) == (False, False)


async def test_a_list_the_other_writer_added_to_is_a_conflict_and_not_replaced(
    mock_vco_client: MockVcoClientFactory,
):
    ds, ds_plan = device_settings()
    qos = qos_module()
    store = a_store(ds, qos, a_guest_segment)
    client = await device_settings_written(mock_vco_client, store, ds, ds_plan)
    held = copy.deepcopy(store.rows[QOS_ID])

    with pytest.raises(ConfigConflictError) as caught:
        _ = await client.write_module(qos, providers_set(qos), rebase=True)

    error = caught.value
    assert methods(store) == [VERSION, GET]
    assert store.rows[QOS_ID] == held  # the Guest segment is still there
    assert (error.stated, error.sent) == (False, False)
    assert "nothing was sent" in error.detail
    assert error.module is not None
    assert [str(c.path) for c in error.lost_edits] == [
        'segments["Global Segment"].webProxy.providers'
    ]


# --- the repair of missing defaults across a rebase ---------------------------


def repaired_defaults(plan: ModulePlan) -> list[dict[str, Any]]:
    return plan.data["segments"][0]["defaults"]


def test_a_rebase_carries_the_repaired_defaults_as_planned():
    qos = qos_module(with_defaults=False)
    plan = rate_set(qos, 5)
    assert len(repaired_defaults(plan)) == 4
    fresh = qos_module(version="q2", enabled=True, with_defaults=False)

    _, rebased_plan, _ = rebase_onto(qos, plan, fresh)

    assert repaired_defaults(rebased_plan) == repaired_defaults(plan)  # same IDs
    assert rebased_plan.changes == plan.changes
    assert rebased_plan.missing_defaults == plan.missing_defaults
    assert [str(c.path) for c in rebased_plan.changes] == ["serviceRateLimit.value"]


def test_a_rebase_keeps_the_repair_turned_off():
    qos = qos_module(with_defaults=False)
    plan = plan_module(qos, _edited(qos), repair_defaults=False)
    assert plan.repair_defaults is False
    assert "defaults" not in plan.data["segments"][0]
    fresh = qos_module(version="q2", enabled=True, with_defaults=False)

    _, rebased_plan, _ = rebase_onto(qos, plan, fresh)

    assert rebased_plan.repair_defaults is False
    assert "defaults" not in rebased_plan.data["segments"][0]
    assert rebased_plan.changes == plan.changes
    assert [w.code for w in rebased_plan.warnings] == ["defaults_repaired"]
    assert [m.repaired for m in rebased_plan.missing_defaults] == [False]


def _edited(module: QosConfigurationModule[Profile]) -> QosData[Profile]:
    data = module.data.model_copy(deep=True)
    rate = data.service_rate_limit
    assert isinstance(rate, ServiceRateLimit)
    rate.value = 5
    return data
