"""The ref rows an edit adds and removes, by difference of the declared owners, and the complete refs to send.

``derive_refs`` compares the owners of a module as read with the owners now.
It adds a row for an owner that appeared, removes the row of one that went,
and sends the rest of the rows exactly as read. What disagrees as read is
reported and left alone.
"""

import copy
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import MISSING
from pyvelo.client.derived_refs import (
    ALWAYS_SEND_REFS,
    APPEND_ONLY_REF_NAMES,
    PROCESSOR_REF_NAMES,
    SERVICE_TYPES,
    DerivedRefs,
    PlanWarning,
    RefChange,
    changed_owners,
    derive_refs,
    sends_refs_always,
)
from pyvelo.client.models.configuration import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    DeviceSegment,
    DnsBackupProvider,
    DnsPrimaryProvider,
    SegmentAuthentication,
    SegmentDns,
)
from pyvelo.client.models.configuration.ref_owners import fill_ref_only
from pyvelo.client.module_write import wire_dump
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
)
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import ConfigContext
from pyvelo.core.base import IdentityLookup
from pyvelo.core.identity import (
    NetworkServiceIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.layers import Edge
from pyvelo.testing import make_module_json
from pyvelo.testing.fixtures import make_network_service_resolver
from refs_helpers import (
    AUTH,
    CONFIG,
    CSS,
    DNS_A,
    DNS_B,
    DS_ID,
    SEG0,
    SEG0_OBJECT,
    SEG1,
    SEG1_OBJECT,
    dns,
    ds_module,
    segment_entry,
    segment_row,
    service_row,
    table_registry,
)

PRIMARY = "deviceSettings:dns:primaryProvider"
BACKUP = "deviceSettings:dns:backupProvider"
AUTHENTICATION = "deviceSettings:authentication"
CSS_PROVIDER = "deviceSettings:css:provider"
SEGMENT = "deviceSettings:segment"
PRIMARY_PATH = 'segments["Global Segment"].dns.primary_provider.service'

DNS_A_IDENTITY = NetworkServiceIdentity("dns-a", UUID(DNS_A), 701, "dns")
DNS_B_IDENTITY = NetworkServiceIdentity("dns-b", UUID(DNS_B), 702, "dns")
AUTH_IDENTITY = NetworkServiceIdentity("radius-1", UUID(AUTH), 703, "authentication")
CSS_IDENTITY = NetworkServiceIdentity("css-1", UUID(CSS), 704, "cloudSecurityService")
SEG0_IDENTITY = SegmentIdentity("Global Segment", 0, SEG0_OBJECT, UUID(SEG0))
SEG1_IDENTITY = SegmentIdentity("Voice", 1, SEG1_OBJECT, UUID(SEG1))


def derive(
    module: ConfigurationModuleBase,
    current: DeviceSettingsData | None,
    resolvers: IdentityLookup | None = None,
    *,
    payload: dict[str, Any] | None = None,
) -> DerivedRefs:
    """``derive_refs`` for *module*'s data as read against *current*, at the module's level."""
    data = module.data  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(data, DeviceSettingsData)
    level = data.config_context
    baseline = DeviceSettingsData.model_validate(
        module.raw_data, context={"config_level": level}
    )
    if module.refs_loaded:
        fill_ref_only(baseline, module.raw_refs)
    if payload is None:
        payload = {} if current is None else wire_dump(current, level)
    return derive_refs(
        module,
        baseline,
        current,
        payload,
        level=level,
        resolvers=resolvers,
    )


def edited(module: DeviceSettingsConfigurationModule) -> DeviceSettingsData:
    """A deep copy of the module's data, to edit."""
    return module.data.model_copy(deep=True)


def dns_of(data: DeviceSettingsData, index: int = 0) -> SegmentDns:
    found = data.segments[index].dns
    assert isinstance(found, SegmentDns)
    return found


def set_primary(data: DeviceSettingsData, value: UUID | None, index: int = 0) -> None:
    provider = dns_of(data, index).primary_provider
    assert isinstance(provider, DnsPrimaryProvider)
    provider.service = value


def set_css(data: DeviceSettingsData, value: UUID | str, index: int = 0) -> None:
    css = data.segments[index].css
    assert css is not MISSING and css.provider is not MISSING
    css.provider.logical_id = cast(Any, value)


def primary_row(
    logical_id: str = DNS_A,
    object_id: int = 701,
    segment_object_id: int = SEG0_OBJECT,
    segment_logical_id: str = SEG0,
) -> dict[str, Any]:
    return service_row(
        PRIMARY, logical_id, object_id, segment_object_id, segment_logical_id
    )


def backup_row() -> dict[str, Any]:
    return service_row(BACKUP, DNS_B, 702, SEG0_OBJECT, SEG0)


def hub_row(logical_id: str, object_id: int) -> dict[str, Any]:
    """A row of a name nothing declares; its type is the one its typed ref wants."""
    row = service_row(
        "deviceSettings:vpn:edgeHub", logical_id, object_id, SEG0_OBJECT, SEG0
    )
    return {**row, "type": "edgeHub"}


def global_refs(**extra: Any) -> dict[str, Any]:
    return {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0), **extra}


def primary_module(**refs: Any) -> DeviceSettingsConfigurationModule:
    """A module with a primary provider on the Global Segment and the row of it."""
    return ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_refs(**{PRIMARY: primary_row(), **refs}),
    )


def refs_of(derived: DerivedRefs) -> dict[str, Any]:
    assert derived.refs is not None
    return derived.refs


def test_the_name_sets_are_the_ones_of_the_plan():
    assert APPEND_ONLY_REF_NAMES == {
        "deviceSettings:vnfs:edge",
        "objectGroup:addressGroup",
        "objectGroup:portGroup",
    }
    assert len(PROCESSOR_REF_NAMES) == 13
    assert "deviceSettings:vnfs:edge" in PROCESSOR_REF_NAMES
    assert "deviceSettings:wssIntegration" in PROCESSOR_REF_NAMES
    assert ALWAYS_SEND_REFS == {"deviceSettings", "QOS"}


async def test_setting_a_provider_adds_exactly_one_five_field_row():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0)},
    )
    current = edited(module)
    set_primary(current, UUID(DNS_A))
    derived = derive(module, current, await table_registry(DNS_A_IDENTITY))
    (change,) = derived.changes
    assert change.added and change.name == PRIMARY
    assert change.row == {
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": 701,
        "ref": PRIMARY,
        "segmentObjectId": SEG0_OBJECT,
    }
    assert change.owner == PRIMARY_PATH
    assert change.segment == "Global Segment"
    assert change.match == "logicalId"
    assert change.value == DNS_A
    assert change.segment_object_id == SEG0_OBJECT
    assert refs_of(derived)[SEGMENT] == segment_row(0, SEG0_OBJECT, SEG0)  # bare
    assert refs_of(derived)[PRIMARY] == [change.row]
    assert derived.warnings == ()


async def test_changing_a_provider_swaps_its_row_and_keeps_the_others():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True, backup=True))],
        global_refs(**{PRIMARY: primary_row(), BACKUP: backup_row()}),
    )
    current = edited(module)
    set_primary(current, UUID(DNS_B))
    derived = derive(module, current, await table_registry(DNS_B_IDENTITY))
    removed = [c for c in derived.changes if not c.added]
    added = [c for c in derived.changes if c.added]
    assert len(derived.changes) == 2
    assert [c.row for c in removed] == [primary_row()]
    assert removed[0].value == DNS_A and removed[0].owner == PRIMARY_PATH
    assert removed[0].segment_object_id == SEG0_OBJECT
    assert added[0].row["enterpriseObjectId"] == 702
    assert added[0].value == DNS_B
    refs = refs_of(derived)
    assert refs[PRIMARY] == [added[0].row]
    assert refs[BACKUP] == backup_row()  # untouched: bare, as read
    assert refs[SEGMENT] == segment_row(0, SEG0_OBJECT, SEG0)


def test_clearing_a_provider_removes_its_row_and_leaves_its_name_out():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True, backup=True))],
        global_refs(**{PRIMARY: primary_row(), BACKUP: backup_row()}),
    )
    current = edited(module)
    set_primary(current, None)
    derived = derive(module, current)
    (change,) = derived.changes
    assert not change.added and change.row == primary_row()
    assert change.owner == PRIMARY_PATH and change.value == DNS_A
    refs = refs_of(derived)
    assert PRIMARY not in refs
    assert refs[BACKUP] == backup_row()


async def test_an_owns_ref_identity_added_and_removed():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, css={"provider": {"logicalId": ""}})],
        global_refs(),
    )
    current = edited(module)
    set_css(current, UUID(CSS))
    derived = derive(module, current, await table_registry(CSS_IDENTITY))
    (added,) = derived.changes
    assert added.added and added.name == CSS_PROVIDER and added.owner is None
    assert added.row == {
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": 704,
        "ref": CSS_PROVIDER,
        "segmentObjectId": SEG0_OBJECT,
    }
    assert added.value == CSS and added.match == "logicalId"

    with_row = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}}
            )
        ],
        global_refs(
            **{CSS_PROVIDER: service_row(CSS_PROVIDER, CSS, 704, SEG0_OBJECT, SEG0)}
        ),
    )
    cleared = edited(with_row)
    set_css(cleared, "")
    derived = derive(with_row, cleared)
    (removed,) = derived.changes
    assert not removed.added and removed.name == CSS_PROVIDER
    assert removed.row["enterpriseObjectId"] == 704
    assert CSS_PROVIDER not in refs_of(derived)


def test_no_edit_to_an_owner_changes_nothing():
    module = primary_module(**{BACKUP: backup_row()})
    derived = derive(module, edited(module))
    assert derived.changes == () and derived.warnings == ()
    assert derived.refs == module.raw_refs


def test_untouched_names_keep_their_shape_and_changed_ones_are_lists():
    two = [
        hub_row(DNS_A, 801),
        hub_row(DNS_B, 802),
    ]
    module = primary_module(**{"deviceSettings:vpn:edgeHub": two, BACKUP: backup_row()})
    current = edited(module)
    set_primary(current, None)
    refs = refs_of(derive(module, current))
    assert refs["deviceSettings:vpn:edgeHub"] == two  # a list stays a list
    assert refs[BACKUP] == backup_row()  # a bare object stays bare
    assert refs[SEGMENT] == segment_row(0, SEG0_OBJECT, SEG0)


def test_append_only_names_are_not_sent():
    module = primary_module(
        **{
            "deviceSettings:vnfs:edge": {
                **service_row(
                    "deviceSettings:vnfs:edge", DNS_A, 810, SEG0_OBJECT, SEG0
                ),
                "type": "securityVnf",
            },
            "objectGroup:addressGroup": {"ref": "objectGroup:addressGroup"},
            "objectGroup:portGroup": [{"ref": "objectGroup:portGroup"}],
        }
    )
    refs = refs_of(derive(module, edited(module)))
    assert not set(refs) & APPEND_ONLY_REF_NAMES
    assert PRIMARY in refs


def test_an_undeclared_name_is_sent_as_read():
    hub = hub_row(DNS_A, 801)
    site = service_row("deviceSettings:css:site", CSS, 704, SEG0_OBJECT, SEG0)
    module = primary_module(
        **{"deviceSettings:vpn:edgeHub": hub, "deviceSettings:css:site": site}
    )
    current = edited(module)
    set_primary(current, None)
    refs = refs_of(derive(module, current))
    assert refs["deviceSettings:vpn:edgeHub"] == hub
    assert refs["deviceSettings:css:site"] == site  # a processor name passes through


@pytest.mark.parametrize("segments", [None, [], "x", {"a": 1}, MISSING])
def test_segments_in_the_payload_must_be_a_non_empty_list(segments: object):
    module = primary_module()
    payload: dict[str, Any] = {} if segments is MISSING else {"segments": segments}
    assert derive(module, edited(module), payload=payload).refs is None


def test_a_module_without_segments_and_no_change_sends_no_refs():
    module = ds_module([], {SEGMENT: segment_row(0, SEG0_OBJECT, SEG0)})
    derived = derive(module, edited(module))
    assert derived.refs is None and derived.changes == ()

    row = make_module_json(
        "firewall", {"segments": []}, configuration_id=CONFIG, refs={"x": {}}
    )
    firewall = FirewallConfigurationModule[Edge].model_validate(row)
    result = derive_refs(
        firewall,
        firewall.data,
        firewall.data.model_copy(deep=True),
        {"segments": [{"segment": {}}]},
        level=ConfigContext.EDGE,
        resolvers=None,
    )
    assert result.refs is None and result.changes == ()


def test_a_new_module_without_rows_to_send_carries_no_refs():
    module = primary_module()
    module._is_new = True  # pyright: ignore[reportPrivateUsage]
    derived = derive(module, edited(module))
    assert derived.changes == () and derived.refs is None


def test_a_plan_of_plain_data_derives_nothing():
    module = primary_module()
    derived = derive(module, None)
    assert derived.changes == () and derived.warnings == ()
    assert derived.refs is None
    full = derive(module, None, payload={"segments": [{}]})
    assert full.refs == module.raw_refs


def test_rows_that_disagree_as_read_warn_and_are_left_alone():
    module = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}}
            )
        ],
        global_refs(
            **{
                # a provider row whose segment the data does not have
                PRIMARY: service_row(PRIMARY, DNS_A, 701, SEG1_OBJECT, SEG1),
            }
        ),
    )
    derived = derive(module, edited(module))
    assert derived.changes == ()
    assert refs_of(derived) == module.raw_refs
    by_code = {w.code: w for w in derived.warnings}
    assert set(by_code) == {"ref_without_owner", "owner_without_ref"}
    assert by_code["ref_without_owner"].path == f"refs {PRIMARY}"
    assert by_code["owner_without_ref"].path == (
        f"refs {CSS_PROVIDER} (Global Segment)"
    )
    for warning in derived.warnings:
        assert isinstance(warning, PlanWarning)
        assert "only what the edit adds or removes" in warning.message


def test_a_row_with_no_owner_in_a_segment_the_data_has_names_the_segment():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(), css={})],
        global_refs(
            **{CSS_PROVIDER: service_row(CSS_PROVIDER, CSS, 704, SEG0_OBJECT, SEG0)}
        ),
    )
    derived = derive(module, edited(module))
    (warning,) = derived.warnings
    assert warning.code == "ref_without_owner"
    assert warning.path == f"refs {CSS_PROVIDER} (Global Segment)"


def test_no_warnings_when_the_refs_were_not_read():
    module = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}}
            )
        ],
        MISSING,
        refs_loaded=False,
    )
    assert derive(module, edited(module)).warnings == ()


def test_two_rows_for_one_provider_warn_and_the_other_row_stays():
    other = primary_row(DNS_B, 702)
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_refs(**{PRIMARY: [primary_row(), other]}),
    )
    assert derive(module, edited(module)).warnings[0].code == "several_refs"
    assert [w.code for w in derive(module, edited(module)).warnings] == ["several_refs"]
    current = edited(module)
    set_primary(current, None)
    derived = derive(module, current)
    (change,) = derived.changes
    assert change.row == primary_row()  # the one the field showed
    assert refs_of(derived)[PRIMARY] == [other]
    assert [w.code for w in derived.warnings] == ["several_refs"]
    assert derived.warnings[0].path == f"refs {PRIMARY} (Global Segment)"


def test_a_removed_row_is_found_by_the_segment_object_id():
    module = ds_module(
        [
            segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True)),
            segment_entry(1, "Voice", SEG1, dns=dns(primary=True)),
        ],
        {
            SEGMENT: [
                segment_row(0, SEG0_OBJECT, SEG0),
                segment_row(1, SEG1_OBJECT, SEG1),
            ],
            PRIMARY: [
                primary_row(),
                primary_row(DNS_A, 701, SEG1_OBJECT, SEG1),
            ],
        },
    )
    current = edited(module)
    set_primary(current, None, 1)
    derived = derive(module, current)
    (change,) = derived.changes
    assert change.segment == "Voice"
    assert change.segment_object_id == SEG1_OBJECT
    assert refs_of(derived)[PRIMARY] == [primary_row()]


def test_a_removed_row_is_found_by_the_segment_logical_id_without_segment_rows():
    module = ds_module(
        [
            segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True)),
            segment_entry(1, "Voice", SEG1, dns=dns(primary=True)),
        ],
        {
            PRIMARY: [
                primary_row(),
                primary_row(DNS_A, 701, SEG1_OBJECT, SEG1),
            ]
        },
    )
    current = edited(module)
    set_primary(current, None, 1)
    derived = derive(module, current)
    (change,) = derived.changes
    assert change.row["segmentLogicalId"] == SEG1
    assert refs_of(derived)[PRIMARY] == [primary_row()]


def test_identities_are_compared_as_lower_case_text():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_refs(**{PRIMARY: primary_row(DNS_A.upper())}),
    )
    assert derive(module, edited(module)).warnings == ()
    current = edited(module)
    set_primary(current, None)
    derived = derive(module, current)
    (change,) = derived.changes
    assert change.value == DNS_A
    assert PRIMARY not in refs_of(derived)


async def test_a_new_row_takes_the_segment_object_id_from_the_resolver():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))], {}
    )
    current = edited(module)
    set_primary(current, UUID(DNS_A))
    registry = await table_registry(DNS_A_IDENTITY, SEG0_IDENTITY)
    (change,) = derive(module, current, registry).changes
    assert change.row["segmentObjectId"] == SEG0_OBJECT
    assert change.segment_object_id == SEG0_OBJECT


async def test_a_new_row_prefers_the_module_s_own_segment_rows_to_the_resolver():
    module = primary_module()
    current = edited(module)
    set_primary(current, UUID(DNS_B))
    other = SegmentIdentity("Global Segment", 0, 999, UUID(SEG0))
    registry = await table_registry(DNS_B_IDENTITY, other)
    added = [c for c in derive(module, current, registry).changes if c.added]
    assert added[0].row["segmentObjectId"] == SEG0_OBJECT


async def test_a_new_authentication_row():
    module = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, authentication={"ref": AUTHENTICATION}
            )
        ],
        global_refs(),
    )
    current = edited(module)
    authentication = current.segments[0].authentication
    assert isinstance(authentication, SegmentAuthentication)
    authentication.service = UUID(AUTH)
    (change,) = derive(module, current, await table_registry(AUTH_IDENTITY)).changes
    assert change.name == AUTHENTICATION and change.row["enterpriseObjectId"] == 703


def set_backup(data: DeviceSettingsData, value: UUID | None, index: int = 0) -> None:
    provider = dns_of(data, index).backup_provider
    assert isinstance(provider, DnsBackupProvider)
    provider.service = value


def set_authentication(data: DeviceSettingsData, value: UUID) -> None:
    authentication = data.segments[0].authentication
    assert isinstance(authentication, SegmentAuthentication)
    authentication.service = value


async def provider_edit(name: str, service: NetworkServiceIdentity) -> DerivedRefs:
    """``derive_refs`` of the edit that sets the owner of ref *name* to *service*."""
    stub: dict[str, Any] = {"ref": name}
    sections: dict[str, Any] = {
        PRIMARY: {"dns": dns(primary=True)},
        BACKUP: {"dns": dns(backup=True)},
        AUTHENTICATION: {"authentication": stub},
        CSS_PROVIDER: {"css": {"provider": {"logicalId": ""}}},
    }
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, **sections[name])], global_refs()
    )
    current = edited(module)
    match name:
        case "deviceSettings:dns:primaryProvider":
            set_primary(current, service.logical_id)
        case "deviceSettings:dns:backupProvider":
            set_backup(current, service.logical_id)
        case "deviceSettings:authentication":
            set_authentication(current, service.logical_id)
        case _:
            set_css(current, service.logical_id)
    return derive(module, current, await table_registry(service))


SERVICE_OF_TYPE = {
    "dns": DNS_A_IDENTITY,
    "authentication": AUTH_IDENTITY,
    "cloudSecurityService": CSS_IDENTITY,
}


def test_the_service_types_are_those_of_the_four_provider_refs():
    assert SERVICE_TYPES == {
        PRIMARY: "dns",
        BACKUP: "dns",
        AUTHENTICATION: "authentication",
        CSS_PROVIDER: "cloudSecurityService",
    }


@pytest.mark.parametrize("name", sorted(SERVICE_TYPES))
async def test_a_provider_of_the_type_its_ref_wants_gets_a_row(name: str):
    wanted = SERVICE_OF_TYPE[SERVICE_TYPES[name]]
    (change,) = (await provider_edit(name, wanted)).changes
    assert change.added and change.name == name
    assert change.row["enterpriseObjectId"] == wanted.object_id


@pytest.mark.parametrize("name", sorted(SERVICE_TYPES))
async def test_a_provider_of_another_type_is_refused_with_the_types_and_the_patch(
    name: str,
):
    wanted = SERVICE_TYPES[name]
    other = next(s for t, s in SERVICE_OF_TYPE.items() if t != wanted)
    with pytest.raises(ResolutionError) as caught:
        _ = await provider_edit(name, other)
    message = str(caught.value)
    assert not isinstance(caught.value, ResolverNotLoadedError)
    assert f"{other.name!r}" in message
    assert f"type {other.type_!r}" in message
    assert f"type {wanted!r}" in message
    assert "name or the logical ID of a service of type" in message
    assert "sets it" in message


def test_a_new_row_without_resolvers_is_not_loaded():
    module = primary_module()
    current = edited(module)
    set_primary(current, UUID(DNS_B))
    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = derive(module, current, None)
    assert caught.value.identity_type is NetworkServiceIdentity


async def test_a_segment_resolver_that_is_not_loaded_is_named():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))], {}
    )
    current = edited(module)
    set_primary(current, UUID(DNS_A))
    registry = ResolverRegistry()
    registry.register(await make_network_service_resolver([DNS_A_IDENTITY]))
    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = derive(module, current, registry)
    assert caught.value.identity_type is SegmentIdentity


async def test_an_unknown_service_names_the_owner():
    module = primary_module()
    current = edited(module)
    set_primary(current, UUID(DNS_B))
    with pytest.raises(ResolutionError) as caught:
        _ = derive(module, current, await table_registry(SEG0_IDENTITY))
    assert not isinstance(caught.value, ResolverNotLoadedError)
    message = str(caught.value)
    assert PRIMARY_PATH in message
    assert "name or the logical ID" in message
    assert DNS_B in message


async def test_an_unknown_segment_names_the_owner():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))], {}
    )
    current = edited(module)
    set_primary(current, UUID(DNS_A))
    with pytest.raises(ResolutionError) as caught:
        _ = derive(module, current, await table_registry(DNS_A_IDENTITY))
    assert PRIMARY_PATH in str(caught.value)


async def test_an_insert_row_has_no_module_id():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_refs(),
    )
    module._is_new = True  # pyright: ignore[reportPrivateUsage]
    current = edited(module)
    set_primary(current, UUID(DNS_A))
    derived = derive(module, current, await table_registry(DNS_A_IDENTITY))
    (change,) = derived.changes
    assert change.row == {
        "configurationId": CONFIG,
        "enterpriseObjectId": 701,
        "ref": PRIMARY,
        "segmentObjectId": SEG0_OBJECT,
    }
    assert refs_of(derived)[PRIMARY] == [change.row]


async def test_the_segment_row_is_derived_on_a_profile_only():
    voice = DeviceSegment.model_validate(segment_entry(1, "Voice", SEG1))
    registry = await table_registry(SEG1_IDENTITY)

    profile = ds_module(
        [segment_entry(0, "Global Segment", SEG0)],
        global_refs(),
        level=ConfigContext.PROFILE,
    )
    current = edited(profile)
    current.segments.append(voice)
    derived = derive(profile, current, registry)
    (change,) = derived.changes
    assert change.added and change.name == SEGMENT and change.owner is None
    assert change.match == "data.segmentId" and change.value == 1
    assert change.segment is None and change.segment_object_id is None
    assert change.row == {
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": SEG1_OBJECT,
        "ref": SEGMENT,
    }
    assert refs_of(derived)[SEGMENT] == [segment_row(0, SEG0_OBJECT, SEG0), change.row]

    edge = ds_module(
        [segment_entry(0, "Global Segment", SEG0)],
        {},
        level=ConfigContext.EDGE,
    )
    current = edited(edge)
    current.segments.append(copy.deepcopy(voice))
    assert derive(edge, current, registry).changes == ()


def test_removing_a_segment_removes_its_segment_row():
    profile = ds_module(
        [
            segment_entry(0, "Global Segment", SEG0),
            segment_entry(1, "Voice", SEG1),
        ],
        {
            SEGMENT: [
                segment_row(0, SEG0_OBJECT, SEG0),
                segment_row(1, SEG1_OBJECT, SEG1),
            ]
        },
    )
    current = edited(profile)
    del current.segments[1]
    derived = derive(profile, current)
    (change,) = derived.changes
    assert not change.added and change.value == 1
    assert change.row == segment_row(1, SEG1_OBJECT, SEG1)
    assert refs_of(derived)[SEGMENT] == [segment_row(0, SEG0_OBJECT, SEG0)]


def test_the_rows_as_read_are_not_changed_by_a_derivation():
    module = primary_module(**{BACKUP: backup_row()})
    before = copy.deepcopy(module.raw_refs)
    current = edited(module)
    set_primary(current, None)
    _ = derive(module, current)
    assert module.raw_refs == before


def test_a_change_is_frozen():
    module = primary_module()
    current = edited(module)
    set_primary(current, None)
    (change,) = derive(module, current).changes
    assert isinstance(change, RefChange)
    with pytest.raises(AttributeError):
        change.name = "x"  # pyright: ignore[reportAttributeAccessIssue]


def test_the_owners_that_changed_need_no_refs_and_no_resolvers():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True, backup=True))],
        MISSING,
        refs_loaded=False,
    )
    data = module.data
    assert isinstance(data, DeviceSettingsData)
    level = data.config_context
    current = edited(module)
    assert changed_owners(data, current, level) == ()
    set_primary(current, UUID(DNS_A))
    (added,) = changed_owners(data, current, level)
    assert (added.name, added.value, added.path) == (
        PRIMARY,
        UUID(DNS_A),
        PRIMARY_PATH,
    )
    (removed,) = changed_owners(current, data, level)
    assert removed.path == PRIMARY_PATH


def test_a_write_always_sends_its_refs_for_a_module_with_segments_that_is_not_new():
    module = primary_module()
    assert sends_refs_always(module, {"segments": [{}]}) is True
    assert sends_refs_always(module, {"segments": []}) is False
    assert sends_refs_always(module, {}) is False
    module._is_new = True  # pyright: ignore[reportPrivateUsage]
    assert sends_refs_always(module, {"segments": [{}]}) is False
    firewall = FirewallConfigurationModule[Edge].model_validate(
        make_module_json("firewall", {"segments": []})
    )
    assert sends_refs_always(firewall, {"segments": [{}]}) is False
