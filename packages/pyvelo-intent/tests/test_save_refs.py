"""A state plans with its own resolvers: provider edits, the services read, and the plan's warnings.

The state is a profile whose device settings module (10 of configuration 100)
has the Global Segment, which the mock client serves as object 1. The store
holds the services ``dns-a`` (701) and ``dns-b`` (702) beside it.
"""

import uuid
from typing import Any, cast

import pytest
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DnsPrimaryProvider,
    SegmentDns,
)
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.core.identity import (
    NetworkServiceIdentity,
    ResolutionError,
    ResolverNotLoadedError,
)
from pyvelo.core.rebase import Path
from pyvelo.testing import ModuleStore, make_module_json
from pyvelo_intent import LayerNotSavedError
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from refs_helpers import (  # pyright: ignore[reportImplicitRelativeImport]
    CONFIG,
    DNS_A,
    DNS_B,
    DS_ID,
    PRIMARY,
    SEGMENT,
    dns,
    segment_entry,
    segment_row,
    service_row,
    table_registry,
)

ZERO = str(uuid.UUID(int=0))
GLOBAL_OBJECT = 1
OWNER = Path.parse('segments["Global Segment"].dns.primary_provider.service')
DATA_OWNER = Path(("data", *OWNER.parts))
SERVICES = "enterprise/getEnterpriseServices"
VERSION_READ = "configuration/getConfigurationModules"
UPDATE = "configuration/updateConfigurationModule"
CSS_PROVIDER = "deviceSettings:css:provider"
NETWORK = {"vlanId": 100, "name": "users", "segmentId": 0, "override": True}
DNS_A_SERVICE = NetworkServiceIdentity(
    name="dns-a", logical_id=uuid.UUID(DNS_A), object_id=701, type_="dns"
)
DNS_B_SERVICE = NetworkServiceIdentity(
    name="dns-b", logical_id=uuid.UUID(DNS_B), object_id=702, type_="dns"
)


def module_json(
    *, module_id: int, configuration_id: int, refs: dict[str, Any], primary: bool
) -> dict[str, Any]:
    data = {
        "lan": {"networks": [dict(NETWORK)], "interfaces": []},
        "segments": [
            segment_entry(0, "Global Segment", ZERO, dns=dns(primary=primary), css={})
        ],
    }
    return make_module_json(
        "deviceSettings",
        data,
        module_id=module_id,
        configuration_id=configuration_id,
        refs=refs,
    )


def refs_of(
    service: str | None, *, configuration_id: int = CONFIG, css: bool = False
) -> dict[str, Any]:
    """The refs of a Global Segment that has *service* as primary provider, or none.

    With *css*, also a row for a CSS provider the data does not have.
    """
    refs: dict[str, Any] = {SEGMENT: segment_row(0, GLOBAL_OBJECT, ZERO)}
    if service is not None:
        refs[PRIMARY] = service_row(PRIMARY, service, 701, GLOBAL_OBJECT, ZERO)
    if css:
        refs[CSS_PROVIDER] = service_row(CSS_PROVIDER, DNS_B, 702, GLOBAL_OBJECT, ZERO)
    return {
        name: {**row, "configurationId": configuration_id} for name, row in refs.items()
    }


def profile_config(
    service: str | None = None, *, css: bool = False
) -> EnterpriseConfigurationProfile:
    return EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": CONFIG,
            "modules": [
                module_json(
                    module_id=DS_ID,
                    configuration_id=CONFIG,
                    refs=refs_of(service, css=css),
                    primary=service is not None,
                )
            ],
        }
    )


def profile_state(service: str | None = None, *, css: bool = False) -> ConfigState:
    return ConfigState(
        edge_config=None,
        profile_config=profile_config(service, css=css),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def served(state: ConfigState) -> MockVcoClient:
    """A client over a store that holds *state*'s configurations and the two services."""
    client = MockVcoClient.for_state(state)
    client.add_service(701, logical_id=DNS_A, name="dns-a", type_="dns")
    client.add_service(702, logical_id=DNS_B, name="dns-b", type_="dns")
    return client


async def loaded_profile(
    service: str | None = None, *, css: bool = False
) -> tuple[ConfigPipeline, MockVcoClient, ConfigState]:
    """A pipeline over a store holding the profile, and the state it loads."""
    client = served(profile_state(service, css=css))
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    client.store.requests.clear()
    return pipeline, client, state


def methods(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests]


def device_settings(state: ConfigState) -> DeviceSettingsData:
    return state.profile.device_settings


def provider_of(layer_data: DeviceSettingsData) -> DnsPrimaryProvider:
    section = layer_data.segments[0].dns
    assert isinstance(section, SegmentDns)
    provider = section.primary_provider
    assert isinstance(provider, DnsPrimaryProvider)
    return provider


def primary_provider(state: ConfigState) -> DnsPrimaryProvider:
    return provider_of(device_settings(state))


def stored_refs(store: ModuleStore) -> dict[str, Any]:
    return store.rows[DS_ID]["refs"]


def one_row(refs: dict[str, Any], name: str) -> dict[str, Any]:
    """The row under *name*: the object, or the only one of a list."""
    held = refs[name]
    (row,) = held if isinstance(held, list) else [held]
    assert isinstance(row, dict)
    return cast(dict[str, Any], row)


def provider_patch(name: str) -> dict[str, Any]:
    return {"dns": {"primaryProvider": {"service": name}}}


async def test_apply_loads_the_network_services_for_a_provider_name():
    pipeline, client, state = await loaded_profile()

    state = await pipeline.apply(
        state,
        module="deviceSettings",
        segment="Global Segment",
        patch=provider_patch("dns-a"),
    )

    assert primary_provider(state).service == uuid.UUID(DNS_A)
    assert methods(client.store).count(SERVICES) == 1
    (change,) = state.plan().changed[0].ref_changes
    assert (change.added, change.name, change.value) == (True, PRIMARY, DNS_A)

    # The table stays loaded: a second patch reads nothing more.
    state = await pipeline.apply(
        state,
        module="deviceSettings",
        segment="Global Segment",
        patch=provider_patch("dns-b"),
    )
    assert primary_provider(state).service == uuid.UUID(DNS_B)
    assert methods(client.store).count(SERVICES) == 1


async def test_an_unknown_provider_name_leaves_the_state_unchanged():
    pipeline, client, state = await loaded_profile()

    with pytest.raises(ResolutionError, match="nope"):
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch("nope"),
        )

    assert methods(client.store).count(SERVICES) == 1  # it was read to find out
    assert primary_provider(state).service is None
    assert state.plan().changed == ()


async def test_a_save_after_setting_a_provider_by_uuid_loads_the_services_once():
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)

    result = await pipeline.save(state)

    assert methods(client.store) == [SERVICES, VERSION_READ, UPDATE]
    (request,) = client.update_requests
    refs = cast(dict[str, Any], request.update.refs)
    assert one_row(refs, PRIMARY) == {
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": 701,
        "ref": PRIMARY,
        "segmentObjectId": GLOBAL_OBJECT,
    }
    assert one_row(stored_refs(client.store), PRIMARY)["logicalId"] == DNS_A
    assert [str(n) for n in result.modules_updated] == ["deviceSettings"]
    assert primary_provider(state).service == uuid.UUID(DNS_A)
    assert state.plan().changed == ()


async def test_a_dry_run_loads_the_services_too():
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)

    result = await pipeline.save(state, dry_run=True)

    assert methods(client.store) == [SERVICES]
    assert result.plan is not None
    (change,) = result.plan.changed[0].ref_changes
    assert (change.added, change.name) == (True, PRIMARY)
    assert f"+ refs {PRIMARY}" in result.plan.render()


async def test_load_costs_no_service_read():
    client = served(profile_state(DNS_A))
    client.store.requests.clear()

    state = await ConfigPipeline(client).load_profile(CONFIG)

    assert SERVICES not in methods(client.store)
    assert not state.resolvers.get_resolver(NetworkServiceResolver).loaded


def test_a_direct_plan_lets_the_not_loaded_error_through():
    state = profile_state()
    primary_provider(state).service = uuid.UUID(DNS_A)

    with pytest.raises(ResolverNotLoadedError) as error:
        _ = state.plan()

    assert error.value.identity_type is NetworkServiceIdentity


async def test_a_second_not_loaded_error_for_the_same_type_propagates():
    class NeverLoads(NetworkServiceResolver):
        loads = 0

        async def load(self, context: ResolverContext) -> None:
            type(self).loads += 1

    pipeline, client, state = await loaded_profile()
    state.resolvers.register(NeverLoads(client))
    primary_provider(state).service = uuid.UUID(DNS_A)

    with pytest.raises(ResolverNotLoadedError):
        _ = await pipeline.save(state)
    with pytest.raises(ResolverNotLoadedError):
        _ = await pipeline.save(state, dry_run=True)
    with pytest.raises(ResolverNotLoadedError):
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch(DNS_B),
        )

    assert NeverLoads.loads == 3  # one load per call, never two
    assert client.update_requests == []
    assert primary_provider(state).service == uuid.UUID(DNS_A)


async def test_plan_warnings_reach_the_plan_and_the_save_result():
    pipeline, _, state = await loaded_profile(css=True)
    plan = state.plan()
    (module_plan,) = plan.modules
    (warning,) = module_plan.warnings
    assert warning.code == "ref_without_owner"
    assert [(w.code, w.path, w.message) for w in plan.warnings] == [
        (warning.code, f"deviceSettings: {warning.path}", warning.message)
    ]
    assert plan.render().count(warning.message) == 1  # the module's block prints it

    # A save that writes nothing, a dry run and a save that write something.
    nothing = await pipeline.save(state)
    assert nothing.warnings == list(plan.warnings)
    device_settings(state).lan.networks[0].name = "renamed"
    dry = await pipeline.save(state, dry_run=True)
    assert dry.warnings == list(plan.warnings)
    saved = await pipeline.save(state)
    assert saved.warnings == list(plan.warnings)


async def test_a_data_edit_of_device_settings_keeps_every_row_through_a_processor():
    pipeline, client, state = await loaded_profile(DNS_A)
    client.store.processor_refs[DS_ID] = {}
    device_settings(state).lan.networks[0].name = "renamed"

    _ = await pipeline.save(state)

    refs = stored_refs(client.store)
    assert one_row(refs, SEGMENT)["enterpriseObjectId"] == GLOBAL_OBJECT
    assert one_row(refs, PRIMARY)["logicalId"] == DNS_A
    assert primary_provider(state).service == uuid.UUID(DNS_A)


async def test_field_changes_list_a_provider_change_at_its_path():
    _, _, state = await loaded_profile(DNS_A)
    state.resolvers = await table_registry(DNS_A_SERVICE, DNS_B_SERVICE)

    primary_provider(state).service = uuid.UUID(DNS_B)
    (swap,) = state.get_field_changes()
    assert (swap.module, swap.path, swap.before, swap.after) == (
        "deviceSettings",
        OWNER,
        DNS_A,
        DNS_B,
    )

    primary_provider(state).service = None
    (cleared,) = state.get_field_changes()
    assert (cleared.path, cleared.before, cleared.after) == (OWNER, DNS_A, None)

    primary_provider(state).service = uuid.UUID(DNS_A)
    assert state.get_field_changes() == []


async def test_a_new_provider_is_listed_with_no_before():
    _, _, state = await loaded_profile()
    state.resolvers = await table_registry(DNS_A_SERVICE)
    primary_provider(state).service = uuid.UUID(DNS_A)

    changes = {c.path: c for c in state.get_field_changes("device_settings")}

    assert (changes[OWNER].before, changes[OWNER].after) == (None, DNS_A)


def edge_state() -> ConfigState:
    edge = EdgeConfiguration.model_validate(
        {
            "name": "Edge Specific Profile",
            "id": 50,
            "modules": [
                module_json(
                    module_id=51,
                    configuration_id=50,
                    refs=refs_of(None, configuration_id=50),
                    primary=False,
                )
            ],
        }
    )
    return ConfigState(
        edge_config=edge,
        profile_config=profile_config(),
        target_type="edge",
        resolver_context=ResolverContext(),
    )


async def test_a_provider_edit_on_the_profile_layer_of_an_edge_state_is_not_saved():
    state = edge_state()
    state.take_snapshot()
    client = served(state)
    provider_of(state.profile.device_settings).service = uuid.UUID(DNS_A)

    with pytest.raises(LayerNotSavedError) as error:
        _ = await ConfigPipeline(client).save(state)

    assert error.value.layer == "profile"
    assert str(OWNER) in error.value.fields["deviceSettings"]
    assert client.store.requests == []
    assert state.plan().changed == ()  # reported once


async def test_a_provider_assigned_during_its_write_is_reported():
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)
    during = primary_provider(state)

    def assign_while_writing(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        if new.get("name") == "deviceSettings":
            during.service = uuid.UUID(DNS_B)

    client.store.on_store.append(assign_while_writing)

    with pytest.raises(DetachedObjectError) as error:
        _ = await pipeline.save(state)

    assert error.value.paths == [DATA_OWNER]
    # The write is stored without the assignment, and the state holds what is stored.
    assert one_row(stored_refs(client.store), PRIMARY)["logicalId"] == DNS_A
    assert primary_provider(state).service == uuid.UUID(DNS_A)
    assert state.plan().changed == ()


async def test_a_provider_changed_behind_the_guard_of_a_detached_object_is_reported():
    pipeline, _, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)
    data = device_settings(state)  # compared while the data is alive
    held = provider_of(data)
    _ = await pipeline.save(state)
    with pytest.raises(DetachedObjectError):
        held.service = uuid.UUID(DNS_B)  # an assignment is refused at once

    object.__setattr__(held, "service", uuid.UUID(DNS_B))  # what no guard sees

    with pytest.raises(DetachedObjectError) as error:
        _ = state.plan()
    assert error.value.paths == [DATA_OWNER]
    assert state.plan().changed == ()  # reported once
    assert provider_of(data) is held
