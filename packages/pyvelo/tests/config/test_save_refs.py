"""A state plans with its own resolvers.

Provider edits, the services read, and the plan's warnings.

The state is a profile whose device settings module (10 of configuration 100)
has the Global Segment, which the mock client serves as object 1. The store
holds the services ``dns-a`` (701) and ``dns-b`` (702) beside it.
"""

import uuid
from typing import Any, ClassVar, cast, override

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    CssProviderRef,
    DnsPrimaryProvider,
    SegmentCss,
    SegmentDns,
)
from pyvelo.client.models.configuration.qos.root import QosData, ServiceRateLimit
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config import LayerNotSavedError, PatchError
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import (
    NetworkServiceIdentity,
    ResolutionError,
    ResolverNotLoadedError,
)
from pyvelo.core.layers import Edge, Profile
from pyvelo.core.owned import Owned
from pyvelo.core.rebase import Path
from pyvelo.testing import MockVcoClient, ModuleStore, make_module_json
from refs_helpers import (
    AUTH,
    CONFIG,
    DNS_A,
    DNS_B,
    DS_ID,
    PRIMARY,
    QOS_ID,
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
CONFIGURATION_READ = "configuration/getConfiguration"
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
    """A client over a store that holds *state*'s configurations.

    The store holds the two services too.
    """
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
    return cast("dict[str, Any]", row)


def provider_patch(name: str) -> dict[str, Any]:
    return {"dns": {"primaryProvider": {"service": name}}}


async def test_apply_loads_the_network_services_for_a_provider_name() -> None:
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


async def test_an_unknown_provider_name_leaves_the_state_unchanged() -> None:
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


async def test_a_provider_of_the_wrong_service_type_leaves_the_state_unchanged() -> (
    None
):
    pipeline, client, state = await loaded_profile()
    client.add_service(703, logical_id=AUTH, name="radius-1", type_="authentication")

    with pytest.raises(ResolutionError) as caught:
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch("radius-1"),
        )

    message = str(caught.value)
    assert "'radius-1'" in message
    assert "type 'authentication'" in message
    assert "type 'dns'" in message
    assert primary_provider(state).service is None
    assert state.plan().changed == ()


async def test_a_service_of_the_wrong_type_is_refused_with_what_fits() -> None:
    pipeline, client, state = await loaded_profile()
    client.add_service(703, logical_id=AUTH, name="radius-1", type_="authentication")

    with pytest.raises(ResolutionError) as wrong:
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch("radius-1"),
        )

    error = wrong.value
    assert (error.wanted, error.candidates) == ("dns", ["dns-a", "dns-b"])
    assert error.path == OWNER
    assert str(error).endswith("Of type 'dns' the enterprise has: dns-a, dns-b.")


async def test_a_save_after_setting_a_provider_by_uuid_loads_the_services_once() -> (
    None
):
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)

    result = await pipeline.save(state)

    assert methods(client.store) == [SERVICES, VERSION_READ, UPDATE]
    (request,) = client.update_requests
    refs = cast("dict[str, Any]", request.update.refs)
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


async def test_a_dry_run_loads_the_services_too() -> None:
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)

    result = await pipeline.save(state, dry_run=True)

    assert methods(client.store) == [SERVICES]
    assert result.plan is not None
    (change,) = result.plan.changed[0].ref_changes
    assert (change.added, change.name) == (True, PRIMARY)
    assert f"+ refs {PRIMARY}" in result.plan.render()


async def test_load_costs_no_service_read() -> None:
    client = served(profile_state(DNS_A))
    client.store.requests.clear()

    state = await ConfigPipeline(client).load_profile(CONFIG)

    assert SERVICES not in methods(client.store)
    assert not state.resolvers.get_resolver(NetworkServiceResolver).loaded


def test_a_direct_plan_lets_the_not_loaded_error_through() -> None:
    state = profile_state()
    primary_provider(state).service = uuid.UUID(DNS_A)

    with pytest.raises(ResolverNotLoadedError) as error:
        _ = state.plan()

    assert error.value.identity_type is NetworkServiceIdentity


class NeverLoads(NetworkServiceResolver):
    loads: ClassVar[int] = 0

    @property
    @override
    def loaded(self) -> bool:
        return False

    @override
    async def _load(self, context: ResolverContext) -> None:
        del context
        type(self).loads += 1


async def test_a_second_not_loaded_error_for_the_same_type_propagates() -> None:
    NeverLoads.loads = 0
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


async def test_an_unknown_provider_name_says_where_it_is() -> None:
    pipeline, _, state = await loaded_profile()

    with pytest.raises(ResolutionError) as unknown:
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch("dns-x"),
        )

    error = unknown.value
    assert error.identity_type is NetworkServiceIdentity
    assert str(error.path) == str(OWNER)
    assert str(error).startswith(f"{OWNER}: Unknown network service")


async def test_a_not_loaded_error_reaches_apply_as_it_was_raised() -> None:
    pipeline, client, state = await loaded_profile()
    state.resolvers.register(NeverLoads(client))

    with pytest.raises(ResolverNotLoadedError) as not_loaded:
        _ = await pipeline.apply(
            state,
            module="deviceSettings",
            segment="Global Segment",
            patch=provider_patch("dns-a"),
        )

    assert type(not_loaded.value) is ResolverNotLoadedError
    assert not_loaded.value.path is None
    assert str(not_loaded.value).startswith("The NetworkServiceIdentity lookups")


async def test_plan_warnings_reach_the_plan_and_the_save_result() -> None:
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


async def test_a_data_edit_of_device_settings_keeps_every_row_through_a_processor() -> (
    None
):
    pipeline, client, state = await loaded_profile(DNS_A)
    client.store.processor_refs[DS_ID] = {}
    device_settings(state).lan.networks[0].name = "renamed"

    _ = await pipeline.save(state)

    refs = stored_refs(client.store)
    assert one_row(refs, SEGMENT)["enterpriseObjectId"] == GLOBAL_OBJECT
    assert one_row(refs, PRIMARY)["logicalId"] == DNS_A
    assert primary_provider(state).service == uuid.UUID(DNS_A)


async def test_field_changes_list_a_provider_change_at_its_path() -> None:
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


async def test_a_new_provider_on_a_stub_with_no_ref_lists_the_ref_it_writes() -> None:
    _, _, state = await loaded_profile()
    state.resolvers = await table_registry(DNS_A_SERVICE)
    primary_provider(state).service = uuid.UUID(DNS_A)

    changes = state.get_field_changes("device_settings")

    assert [(c.path, c.before, c.after) for c in changes] == [
        (
            Path.parse('segments["Global Segment"].dns.primary_provider.ref'),
            MISSING,
            PRIMARY,
        ),
        (OWNER, None, DNS_A),
    ]


async def test_a_provider_on_a_stub_that_has_a_ref_is_one_change() -> None:
    _, _, state = await loaded_profile(DNS_A)
    state.resolvers = await table_registry(DNS_A_SERVICE, DNS_B_SERVICE)
    primary_provider(state).service = uuid.UUID(DNS_B)

    changes = state.get_field_changes("device_settings")

    assert [(c.path, c.before, c.after) for c in changes] == [(OWNER, DNS_A, DNS_B)]


CSS_WEST = "c5c5c5c5-c5c5-4c5c-8c5c-c5c5c5c5c5c5"


def other_writer_edit(row: dict[str, Any]) -> None:
    """Another writer's change to a key the caller does not edit."""
    row["data"]["futureKey"] = 1


async def loaded_with_css() -> tuple[ConfigPipeline, MockVcoClient, ConfigState]:
    """``loaded_profile()``, whose enterprise also has the CSS service css-west."""
    pipeline, client, state = await loaded_profile()
    client.add_service(
        710, logical_id=CSS_WEST, name="css-west", type_="cloudSecurityService"
    )
    return pipeline, client, state


async def set_css(
    pipeline: ConfigPipeline, state: ConfigState, value: str | None
) -> ConfigState:
    return await pipeline.apply(
        state,
        module="deviceSettings",
        segment="Global Segment",
        patch={"css": {"provider": {"logical_id": value}}},
    )


def css_provider(state: ConfigState) -> CssProviderRef:
    section = device_settings(state).segments[0].css
    assert isinstance(section, SegmentCss)
    provider = section.provider
    assert isinstance(provider, CssProviderRef)
    return provider


@pytest.mark.parametrize("given", [CSS_WEST, CSS_WEST.upper(), "css-west"])
async def test_a_css_provider_is_set_by_its_logical_id_or_its_name(given: str) -> None:
    pipeline, _, state = await loaded_with_css()

    state = await set_css(pipeline, state, given)

    assert css_provider(state).logical_id == uuid.UUID(CSS_WEST)
    (change,) = state.plan().changed[0].ref_changes
    assert (change.added, change.name, change.value) == (True, CSS_PROVIDER, CSS_WEST)


async def test_empty_text_clears_a_css_provider_and_null_is_refused() -> None:
    pipeline, client, state = await loaded_with_css()
    state = await set_css(pipeline, state, "css-west")
    _ = await pipeline.save(state)
    assert one_row(stored_refs(client.store), CSS_PROVIDER)["enterpriseObjectId"] == 710

    with pytest.raises(PatchError) as refused:
        _ = await set_css(pipeline, state, None)
    assert refused.value.kind == "null"
    assert 'give ""' in refused.value.message
    assert css_provider(state).logical_id == uuid.UUID(CSS_WEST)

    state = await set_css(pipeline, state, "")
    assert css_provider(state).logical_id == ""
    (change,) = state.plan().changed[0].ref_changes
    assert (change.added, change.name, change.value) == (False, CSS_PROVIDER, CSS_WEST)
    _ = await pipeline.save(state)
    assert not stored_refs(client.store).get(CSS_PROVIDER)


async def test_the_redo_of_a_lost_css_provider_edit_applies() -> None:
    pipeline, client, state = await loaded_with_css()
    state = await set_css(pipeline, state, "css-west")
    client.store.other_writer(DS_ID, other_writer_edit)

    with pytest.raises(ConfigConflictError) as lost:
        _ = await pipeline.save(state)

    patch = lost.value.redo_patch()
    assert patch == {
        "segments": {
            "Global Segment": {
                "css": {"provider": {"logicalId": CSS_WEST, "ref": CSS_PROVIDER}}
            }
        }
    }
    state = await pipeline.apply(state, module=lost.value.module_name, patch=patch)
    assert css_provider(state).logical_id == uuid.UUID(CSS_WEST)
    _ = await pipeline.save(state)
    assert one_row(stored_refs(client.store), CSS_PROVIDER)["enterpriseObjectId"] == 710


async def test_the_redo_of_a_lost_css_provider_clear_applies() -> None:
    pipeline, client, state = await loaded_with_css()
    state = await set_css(pipeline, state, "css-west")
    _ = await pipeline.save(state)
    state = await set_css(pipeline, state, "")
    client.store.other_writer(DS_ID, other_writer_edit)

    with pytest.raises(ConfigConflictError) as lost:
        _ = await pipeline.save(state)

    patch = lost.value.redo_patch()
    assert patch == {
        "segments": {"Global Segment": {"css": {"provider": {"logical_id": ""}}}}
    }
    # The row is listed, although the patch redoes the clear that removes it.
    (listed,) = lost.value.not_redone()
    assert (listed.part, str(listed.path).startswith(f'"{CSS_PROVIDER}"')) == (
        "refs",
        True,
    )
    state = await pipeline.apply(state, module=lost.value.module_name, patch=patch)
    assert css_provider(state).logical_id == ""
    _ = await pipeline.save(state)
    assert not stored_refs(client.store).get(CSS_PROVIDER)


async def test_an_unknown_css_name_lists_the_services_the_field_takes() -> None:
    pipeline, client, state = await loaded_with_css()
    client.add_service(
        711,
        logical_id=str(uuid.UUID(int=711)),
        name="css-east",
        type_="cloudSecurityService",
    )

    with pytest.raises(ResolutionError) as near:
        _ = await set_css(pipeline, state, "css-wst")
    with pytest.raises(ResolutionError) as far:
        _ = await set_css(pipeline, state, "zzz")

    assert near.value.wanted == "cloudSecurityService"
    assert near.value.candidates[0] == "css-west"  # the closest first
    assert set(near.value.candidates) <= {"css-west", "css-east"}
    place = 'segments["Global Segment"].css.provider.logical_id'
    assert str(near.value.path) == place
    assert str(near.value).startswith(
        f"{place}: Unknown network service of type 'cloudSecurityService': 'css-wst'."
    )
    assert far.value.candidates == ["css-east", "css-west"]
    assert "Of that type: css-east, css-west" in str(far.value)
    assert "dns-a" not in str(far.value)
    assert state.plan().changed == ()


async def test_an_unknown_css_name_with_no_css_service_says_there_is_none() -> None:
    pipeline, _, state = await loaded_profile()

    with pytest.raises(ResolutionError) as unknown:
        _ = await set_css(pipeline, state, "css-west")

    assert unknown.value.wanted == "cloudSecurityService"
    assert unknown.value.candidates == []
    assert (
        "The enterprise has no network service of type 'cloudSecurityService'."
        in str(unknown.value)
    )


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


async def test_a_provider_edit_on_the_profile_layer_of_an_edge_state_is_not_saved() -> (
    None
):
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


async def test_a_provider_assigned_during_its_write_is_reported() -> None:
    pipeline, client, state = await loaded_profile()
    primary_provider(state).service = uuid.UUID(DNS_A)
    during = primary_provider(state)

    def assign_while_writing(new: dict[str, Any], _old: dict[str, Any] | None) -> None:
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


async def test_a_provider_changed_behind_the_guard_of_a_detached_object_is_reported() -> (  # noqa: E501 (a test's name)
    None
):
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


RATE = {"enabled": False, "inputType": "percent", "value": 1}
EDGE_CONFIG = 50
EDGE_DS_ID = 51
EDGE_QOS_ID = 52


def qos_json(*, module_id: int, configuration_id: int) -> dict[str, Any]:
    """A QoS module with the Global Segment and a rate limit.

    The rate limit is the one value a test edits.
    """
    data = {
        "segments": [
            {
                "segment": {
                    "segmentId": 0,
                    "name": "Global Segment",
                    "type": "REGULAR",
                    "segmentLogicalId": ZERO,
                },
                "rules": [],
            }
        ],
        "serviceRateLimit": dict(RATE),
    }
    return make_module_json(
        "QOS", data, module_id=module_id, configuration_id=configuration_id
    )


def profile_with_qos(*, qos_first: bool = False) -> ConfigState:
    """A profile state with device settings and QoS.

    They are written in that order unless *qos_first*.
    """
    modules = [
        module_json(
            module_id=DS_ID,
            configuration_id=CONFIG,
            refs=refs_of(None),
            primary=False,
        ),
        qos_json(module_id=QOS_ID, configuration_id=CONFIG),
    ]
    if qos_first:
        modules.reverse()
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": CONFIG, "modules": modules}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def edge_with_qos() -> ConfigState:
    """An edge state with device settings and QoS of its own.

    Its profile has only device settings.
    """
    edge = EdgeConfiguration.model_validate(
        {
            "name": "Edge Specific Profile",
            "id": EDGE_CONFIG,
            "modules": [
                module_json(
                    module_id=EDGE_DS_ID,
                    configuration_id=EDGE_CONFIG,
                    refs=refs_of(None, configuration_id=EDGE_CONFIG),
                    primary=False,
                ),
                qos_json(module_id=EDGE_QOS_ID, configuration_id=EDGE_CONFIG),
            ],
        }
    )
    return ConfigState(
        edge_config=edge,
        profile_config=profile_config(),
        target_type="edge",
        resolver_context=ResolverContext(),
    )


def rate_limit(data: QosData[Edge] | QosData[Profile]) -> ServiceRateLimit:
    rate = data.service_rate_limit
    if isinstance(rate, Owned):
        rate = rate.local
    assert isinstance(rate, ServiceRateLimit)
    return rate


def propagating(client: MockVcoClient, qos_id: int) -> None:
    """Make a write of device settings change the QoS row.

    The VCO's propagation does that.
    """

    def enable(row: dict[str, Any]) -> None:
        row["data"]["serviceRateLimit"]["enabled"] = True

    def hook(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings":
            client.store.other_writer(qos_id, enable)

    client.store.on_store.append(hook)


async def test_a_profile_save_rebases_qos_after_device_settings() -> None:
    state = profile_with_qos()
    client = served(state)
    propagating(client, QOS_ID)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    client.store.requests.clear()
    device_settings(state).lan.networks[0].name = "renamed"
    rate_limit(state.profile.qos).value = 5

    result = await pipeline.save(state)

    assert methods(client.store) == [
        VERSION_READ,
        UPDATE,
        VERSION_READ,
        CONFIGURATION_READ,
        UPDATE,
    ]
    (_, qos_request) = client.store.requests[-1]
    assert qos_request["id"] == QOS_ID
    # The version the propagation left, not the one read at load.
    assert qos_request["version"] not in ("v1", client.store.rows[QOS_ID]["version"])
    assert [str(n) for n in result.modules_updated] == ["deviceSettings", "QOS"]
    # The propagation and the edit are both stored and both in the state.
    held = client.store.rows[QOS_ID]["data"]["serviceRateLimit"]
    assert held == {**RATE, "enabled": True, "value": 5}
    rate = rate_limit(state.profile.qos)
    assert (rate.enabled, rate.value) == (True, 5)
    assert state.plan().changed == ()
    (change,) = result.server_changes[ConfigurationModuleName.QOS]
    assert (str(change.path), change.as_read, change.stored, change.merged) == (
        "serviceRateLimit.enabled",
        False,
        True,
        True,
    )


async def test_a_later_qos_save_is_rebased_onto_what_the_device_settings_write_left() -> (  # noqa: E501 (a test's name)
    None
):
    state = profile_with_qos()
    client = served(state)
    propagating(client, QOS_ID)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    device_settings(state).lan.networks[0].name = "renamed"
    first = await pipeline.save(state)
    assert [str(n) for n in first.modules_updated] == ["deviceSettings"]
    assert "QOS" in state.rebase_on_write
    assert "deviceSettings" not in state.rebase_on_write
    _ = await pipeline.revert(state, module="qos")
    assert "QOS" in state.rebase_on_write
    client.store.requests.clear()
    rate_limit(state.profile.qos).value = 5

    second = await pipeline.save(state)

    assert methods(client.store) == [VERSION_READ, CONFIGURATION_READ, UPDATE]
    assert [str(n) for n in second.modules_updated] == ["QOS"]
    held = client.store.rows[QOS_ID]["data"]["serviceRateLimit"]
    assert held == {**RATE, "enabled": True, "value": 5}
    rate = rate_limit(state.profile.qos)
    assert (rate.enabled, rate.value) == (True, 5)
    assert state.rebase_on_write == set()
    assert state.plan().changed == ()


async def test_a_later_qos_conflict_names_the_device_settings_write_not_this_save() -> (
    None
):
    state = profile_with_qos()
    client = served(state)
    pipeline = ConfigPipeline(client)

    def raise_rate(row: dict[str, Any]) -> None:
        row["data"]["serviceRateLimit"]["value"] = 7

    def hook(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings":
            client.store.other_writer(QOS_ID, raise_rate)

    client.store.on_store.append(hook)
    state = await pipeline.load_profile(CONFIG)
    device_settings(state).lan.networks[0].name = "renamed"
    _ = await pipeline.save(state)
    client.store.requests.clear()
    rate_limit(state.profile.qos).value = 5

    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)

    error = caught.value
    assert methods(client.store) == [VERSION_READ, CONFIGURATION_READ]
    assert (error.stated, error.sent) == (False, False)
    assert [str(c.path) for c in error.lost_edits] == ["serviceRateLimit.value"]
    assert client.store.rows[QOS_ID]["data"]["serviceRateLimit"]["value"] == 7
    assert "this save wrote device settings" not in str(error)
    assert error.detail == (
        "the module changed on the VCO after a device settings write of this "
        "profile, at a path this save also changes; nothing was sent"
    )


async def test_a_device_settings_write_stored_otherwise_than_sent_marks_qos() -> None:
    """The write raised, but the VCO stored it: the later QoS save is rebased."""
    state = profile_with_qos()
    client = served(state)
    propagating(client, QOS_ID)

    def otherwise(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings":
            new["data"]["lan"]["networks"][0]["name"] = "stored-otherwise"

    client.store.on_store.append(otherwise)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    device_settings(state).lan.networks[0].name = "renamed"
    with pytest.raises(WriteNotAppliedError):
        _ = await pipeline.save(state)
    assert client.store.rows[QOS_ID]["data"]["serviceRateLimit"]["enabled"] is True
    assert "QOS" in state.rebase_on_write
    client.store.requests.clear()
    rate_limit(state.profile.qos).value = 5

    result = await pipeline.save(state)

    assert methods(client.store) == [VERSION_READ, CONFIGURATION_READ, UPDATE]
    assert [str(n) for n in result.modules_updated] == ["QOS"]
    held = client.store.rows[QOS_ID]["data"]["serviceRateLimit"]
    assert held == {**RATE, "enabled": True, "value": 5}
    assert state.rebase_on_write == set()


async def test_a_device_settings_write_whose_outcome_is_unknown_marks_qos() -> None:
    """An answer that cannot be read leaves the write possibly stored."""
    state = profile_with_qos()
    client = served(state)

    def unreadable(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings":
            new["data"]["lan"] = "not an object"

    client.store.on_store.append(unreadable)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    device_settings(state).lan.networks[0].name = "renamed"

    with pytest.raises(WriteOutcomeUnknownError) as caught:
        _ = await pipeline.save(state)

    assert caught.value.answer == "row"
    assert "QOS" in state.rebase_on_write


async def test_a_device_settings_write_refused_as_a_conflict_marks_nothing() -> None:
    """Nothing of a refused write is stored, so nothing it could change is marked."""
    state = profile_with_qos()
    client = served(state)
    propagating(client, QOS_ID)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)

    def theirs(row: dict[str, Any]) -> None:
        row["data"]["lan"]["networks"][0]["name"] = "theirs"

    client.store.other_writer(DS_ID, theirs)
    device_settings(state).lan.networks[0].name = "renamed"

    with pytest.raises(ConfigConflictError):
        _ = await pipeline.save(state)

    assert client.store.rows[QOS_ID]["data"]["serviceRateLimit"]["enabled"] is False
    assert state.rebase_on_write == set()


async def test_a_profile_save_raises_a_conflict_where_the_propagation_reached_an_edit() -> (  # noqa: E501 (a test's name)
    None
):
    state = profile_with_qos()
    client = served(state)
    pipeline = ConfigPipeline(client)

    def raise_rate(row: dict[str, Any]) -> None:
        row["data"]["serviceRateLimit"]["value"] = 7

    def hook(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        del old
        if new["name"] == "deviceSettings":
            client.store.other_writer(QOS_ID, raise_rate)

    client.store.on_store.append(hook)
    state = await pipeline.load_profile(CONFIG)
    device_settings(state).lan.networks[0].name = "renamed"
    rate_limit(state.profile.qos).value = 5

    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)

    error = caught.value
    assert (error.stated, error.sent) == (False, False)
    assert "after a device settings write of this profile" in error.detail
    assert error.module is not None
    assert [str(c.path) for c in error.lost_edits] == ["serviceRateLimit.value"]
    assert client.store.rows[QOS_ID]["data"]["serviceRateLimit"]["value"] == 7
    assert len(client.update_requests) == 1  # device settings; QoS was not sent
    # The state holds the module as it is now, with the clash to redo on it.
    assert rate_limit(state.profile.qos).value == 7
    assert "QOS" not in state.rebase_on_write


async def test_a_module_listed_before_device_settings_is_written_after_it_and_rebased() -> (  # noqa: E501 (a test's name)
    None
):
    state = profile_with_qos(qos_first=True)
    client = served(state)
    propagating(client, QOS_ID)
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(CONFIG)
    client.store.requests.clear()
    device_settings(state).lan.networks[0].name = "renamed"
    rate_limit(state.profile.qos).value = 5

    result = await pipeline.save(state)

    assert methods(client.store) == [
        VERSION_READ,
        UPDATE,
        VERSION_READ,
        CONFIGURATION_READ,
        UPDATE,
    ]
    assert [r.configuration_module_id for r in client.update_requests] == [
        DS_ID,
        QOS_ID,
    ]
    assert [str(n) for n in result.modules_updated] == ["deviceSettings", "QOS"]


async def test_an_edge_save_does_not_rebase() -> None:
    state = edge_with_qos()
    state.take_snapshot()
    client = served(state)
    propagating(client, EDGE_QOS_ID)
    pipeline = ConfigPipeline(client)
    device_settings_data = state.edge.device_settings
    device_settings_data.lan.networks[0].name = "renamed"
    rate_limit(state.edge.qos).value = 5
    client.store.requests.clear()

    with pytest.raises(ConfigConflictError) as caught:
        _ = await pipeline.save(state)

    # The version read finds the QoS module changed; it is a conflict, not a rebase.
    assert methods(client.store) == [
        VERSION_READ,
        UPDATE,
        VERSION_READ,
        CONFIGURATION_READ,
    ]
    assert (caught.value.stated, caught.value.sent) == (False, False)
    assert "after a device settings write of this profile" not in caught.value.detail
