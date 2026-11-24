"""The device settings fields that own ref rows, how they are found, and how a module fills its ``RefOnly`` fields.

A provider's data holds a stub (``{"ref": "deviceSettings:dns:primaryProvider"}``)
and nothing that says which service it is: the row of that name says it. A
module read with its refs fills ``service`` from the row of its segment.
"""

import copy
import uuid
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import MISSING
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.models.configuration import (
    DeviceSettingsData,
    FirewallData,
    QosData,
    SegmentMetadata,
    WanData,
)
from pyvelo.client.models.configuration.control_plane import ControlPlaneData
from pyvelo.client.models.configuration.device_settings.segment import (
    CssProviderRef,
    DeviceSegment,
    DnsBackupProvider,
    DnsPrimaryProvider,
    DnsProvider,
    SegmentAuthentication,
    SegmentDns,
)
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge, QosRule
from pyvelo.client.models.configuration.ref_owners import (
    RefOwner,
    can_hold_owner,
    fill_ref_only,
    ref_only_values,
    ref_owners,
    segment_object_ids,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleList,
    ConfigurationModuleUpdate,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    GetConfiguration,
    GetConfigurationModules,
    InsertFirewallConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext, OwnsRef, RefOnly
from pyvelo.core.fields import get_owns_ref, get_ref_only, get_resolvable
from pyvelo.core.identity import NetworkServiceIdentity, SegmentIdentity
from pyvelo.core.layers import Profile
from pyvelo.testing import make_module_json
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
    store_with_services,
)

from ._type_support import MockVcoClientFactory

PRIMARY = "deviceSettings:dns:primaryProvider"
BACKUP = "deviceSettings:dns:backupProvider"
AUTHENTICATION = "deviceSettings:authentication"
CSS_PROVIDER = "deviceSettings:css:provider"
PRIMARY_PATH = 'segments["Global Segment"].dns.primary_provider.service'


def primary_row(logical_id: str = DNS_A, object_id: int = 701) -> dict[str, Any]:
    return service_row(PRIMARY, logical_id, object_id, SEG0_OBJECT, SEG0)


def global_segment_refs(**extra: Any) -> dict[str, Any]:
    return {"deviceSettings:segment": segment_row(0, SEG0_OBJECT, SEG0), **extra}


def primary_module(**kw: Any) -> DeviceSettingsConfigurationModule:
    """A module with a primary provider on the Global Segment, and the row of it."""
    return ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_segment_refs(**{PRIMARY: primary_row()}),
        **kw,
    )


def dns_of(data: DeviceSettingsData, index: int = 0) -> SegmentDns:
    found = data.segments[index].dns
    assert isinstance(found, SegmentDns)
    return found


def primary_of(
    module: DeviceSettingsConfigurationModule | DeviceSettingsData, index: int = 0
) -> DnsPrimaryProvider:
    data = module if isinstance(module, DeviceSettingsData) else module.data
    found = dns_of(data, index).primary_provider
    assert isinstance(found, DnsPrimaryProvider)
    return found


def backup_of(module: DeviceSettingsConfigurationModule) -> DnsBackupProvider:
    found = dns_of(module.data).backup_provider
    assert isinstance(found, DnsBackupProvider)
    return found


def owner_of(owners: tuple[RefOwner, ...], name: str) -> RefOwner:
    (found,) = [o for o in owners if o.name == name]
    return found


# --- the fill -------------------------------------------------------------


def test_a_provider_is_filled_from_the_row_of_its_segment():
    module = primary_module()
    assert primary_of(module).service == UUID(DNS_A)


def test_a_provider_is_filled_with_the_row_of_its_own_segment_only():
    module = ds_module(
        [
            segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True)),
            segment_entry(1, "Voice", SEG1, dns=dns(primary=True)),
        ],
        {
            "deviceSettings:segment": [
                segment_row(0, SEG0_OBJECT, SEG0),
                segment_row(1, SEG1_OBJECT, SEG1),
            ],
            PRIMARY: [
                service_row(PRIMARY, DNS_B, 702, SEG1_OBJECT, SEG1),
                primary_row(),
            ],
        },
    )
    services = [primary_of(module, index).service for index in (0, 1)]
    assert services == [UUID(DNS_A), UUID(DNS_B)]


def test_a_provider_with_no_row_is_none():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_segment_refs(),
    )
    provider = primary_of(module)
    assert provider.service is None


def test_a_module_with_no_refs_key_has_none_for_every_provider():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))], MISSING
    )
    provider = primary_of(module)
    assert provider.service is None


def test_a_module_whose_refs_were_not_read_leaves_the_field_missing():
    module = primary_module(refs_loaded=False)
    provider = primary_of(module)
    assert provider.service is MISSING
    assert module.refs_loaded is False
    assert primary_module().refs_loaded is True


def test_refs_loaded_is_in_no_dump():
    module = primary_module(refs_loaded=False)
    assert "refs_loaded" not in module.model_dump()
    assert "refsLoaded" not in module.model_dump(by_alias=True)
    assert "service" not in str(module.data.model_dump(mode="json", by_alias=True))


def test_a_module_validated_with_no_context_counts_as_loaded():
    row = primary_module().model_dump(mode="json", by_alias=True)
    module = DeviceSettingsConfigurationModule.model_validate(row)
    assert module.refs_loaded is True


def test_the_level_of_the_context_is_kept_beside_refs_loaded():
    module = primary_module(refs_loaded=False, level=ConfigContext.EDGE)
    assert module.config_context is ConfigContext.EDGE
    assert module.data.config_context is ConfigContext.EDGE


def test_data_validated_alone_leaves_the_field_missing():
    module = primary_module()
    again = type(module.data).model_validate(
        module.data.model_dump(mode="json", by_alias=True)
    )
    assert primary_of(again).service is MISSING


def test_fill_falls_back_to_the_segment_logical_id():
    # No deviceSettings:segment row: the row's segmentLogicalId names the segment.
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        {PRIMARY: primary_row()},
    )
    assert primary_of(module).service == UUID(DNS_A)


def test_a_row_of_another_segment_is_not_taken_by_logical_id_when_its_object_is_known():
    # The segment rows say object 502 is segment 1; the row's logical ID is
    # that of segment 0, but the object ID is looked at first.
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        {
            "deviceSettings:segment": [
                segment_row(0, SEG0_OBJECT, SEG0),
                segment_row(1, SEG1_OBJECT, SEG1),
            ],
            PRIMARY: service_row(PRIMARY, DNS_A, 701, SEG1_OBJECT, SEG0),
        },
    )
    assert primary_of(module).service is None


def test_the_first_matching_row_in_read_order_is_taken():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_segment_refs(
            **{
                PRIMARY: [
                    primary_row(DNS_B, 702),
                    primary_row(DNS_A, 701),
                ]
            }
        ),
    )
    assert primary_of(module).service == UUID(DNS_B)


def test_a_stub_ref_names_the_row():
    module = ds_module(
        [
            segment_entry(
                0,
                "Global Segment",
                SEG0,
                dns={
                    **dns(),
                    "primaryProvider": {"ref": BACKUP},
                },
            )
        ],
        global_segment_refs(
            **{
                PRIMARY: primary_row(DNS_A, 701),
                BACKUP: service_row(BACKUP, DNS_B, 702, SEG0_OBJECT, SEG0),
            }
        ),
    )
    assert primary_of(module).service == UUID(DNS_B)
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    primary = [o for o in owners if o.path.endswith("primary_provider.service")]
    assert [o.name for o in primary] == [BACKUP]


def test_an_effective_style_stub_with_no_ref_is_filled_and_keeps_no_ref():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns())],
        global_segment_refs(
            **{BACKUP: service_row(BACKUP, DNS_B, 702, SEG0_OBJECT, SEG0)}
        ),
    )
    backup = backup_of(module)
    assert backup.service == UUID(DNS_B)
    assert backup.ref is MISSING
    assert "service" not in backup.model_fields_set


def test_authentication_is_filled_from_its_row():
    module = ds_module(
        [
            segment_entry(
                0,
                "Global Segment",
                SEG0,
                authentication={"ref": AUTHENTICATION},
            )
        ],
        global_segment_refs(
            **{
                AUTHENTICATION: service_row(
                    AUTHENTICATION, AUTH, 703, SEG0_OBJECT, SEG0
                )
            }
        ),
    )
    authentication = module.data.segments[0].authentication
    assert isinstance(authentication, SegmentAuthentication)
    assert authentication.service == UUID(AUTH)


def test_a_field_outside_any_segment_takes_a_row_with_no_segment():
    holder = DnsPrimaryProvider(ref=PRIMARY)
    refs = {
        PRIMARY: [
            primary_row(DNS_A, 701),
            {**primary_row(DNS_B, 702), "segmentObjectId": None},
        ]
    }
    fill_ref_only(holder, refs)
    assert holder.service == UUID(DNS_B)
    refs = {PRIMARY: [primary_row(DNS_A, 701)]}
    fill_ref_only(holder, refs)
    assert holder.service is None


def test_the_fill_is_not_an_edit():
    module = primary_module()
    assert plan_module(module).operation == "none"
    provider = primary_of(module)
    assert provider.ref == PRIMARY
    assert provider.model_fields_set == {"ref"}


def test_the_data_is_dumped_as_it_was_read():
    module = primary_module()
    dumped = module.data.model_dump(mode="json", by_alias=True)
    raw = module.raw_data
    assert isinstance(raw, dict)
    assert dumped["segments"][0]["dns"]["primaryProvider"] == {"ref": PRIMARY}
    assert (
        dumped["segments"][0]["dns"]["primaryProvider"]
        == (raw["segments"][0]["dns"]["primaryProvider"])
    )


# --- the declarations ------------------------------------------------------


def test_the_declarations_are_the_specs():
    assert get_owns_ref(DeviceSegment.model_fields["segment"]) == OwnsRef(
        "deviceSettings:segment",
        SegmentIdentity,
        by="segment_id",
        value="segment_id",
        row="data.segmentId",
        level=ConfigContext.PROFILE,
    )
    assert get_owns_ref(CssProviderRef.model_fields["logical_id"]) == OwnsRef(
        CSS_PROVIDER,
        NetworkServiceIdentity,
        by="logical_id",
        row="logicalId",
        per_segment=True,
    )
    css_resolvable = get_resolvable(CssProviderRef.model_fields["logical_id"])
    assert css_resolvable is not None
    assert css_resolvable.identity_type is NetworkServiceIdentity
    assert css_resolvable.id_attr == "logical_id"
    for cls, name in (
        (SegmentAuthentication, AUTHENTICATION),
        (DnsPrimaryProvider, PRIMARY),
        (DnsBackupProvider, BACKUP),
    ):
        field = cls.model_fields["service"]
        assert get_ref_only(field) == RefOnly(default=name)
        resolvable = get_resolvable(field)
        assert resolvable is not None
        assert resolvable.identity_type is NetworkServiceIdentity
    assert issubclass(DnsPrimaryProvider, DnsProvider)
    assert issubclass(DnsBackupProvider, DnsProvider)


# --- the owners ------------------------------------------------------------


def test_owners_found_in_a_profile_module():
    module = primary_module()
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    assert {o.name for o in owners} == {"deviceSettings:segment", PRIMARY}
    segment = owner_of(owners, "deviceSettings:segment")
    assert segment.value == 0
    assert segment.segment_id is None and segment.segment_name is None
    assert segment.identity_type is SegmentIdentity
    assert isinstance(segment.marker, OwnsRef)
    provider = owner_of(owners, PRIMARY)
    assert provider.value == UUID(DNS_A)
    assert provider.segment_id == 0
    assert provider.segment_name == "Global Segment"
    assert provider.identity_type is NetworkServiceIdentity
    assert provider.marker == RefOnly(default=PRIMARY)
    assert provider.path == PRIMARY_PATH


def test_level_keeps_the_segment_name_from_being_derived_on_an_edge():
    module = primary_module(level=ConfigContext.EDGE)
    owners = ref_owners(module.data, ConfigContext.EDGE)
    assert [o.name for o in owners] == [PRIMARY]
    assert [o.name for o in ref_owners(module.data, None)] == [PRIMARY]


def test_a_segment_is_the_one_of_the_item_the_owner_sits_in():
    module = ds_module(
        [
            segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True)),
            segment_entry(1, "Voice", SEG1, dns=dns(primary=True)),
        ],
        {
            "deviceSettings:segment": [
                segment_row(0, SEG0_OBJECT, SEG0),
                segment_row(1, SEG1_OBJECT, SEG1),
            ],
            PRIMARY: [
                service_row(PRIMARY, DNS_B, 702, SEG1_OBJECT, SEG1),
                primary_row(),
            ],
        },
    )
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    found = {
        (o.segment_id, o.segment_name, o.value) for o in owners if o.name == PRIMARY
    }
    assert found == {(0, "Global Segment", UUID(DNS_A)), (1, "Voice", UUID(DNS_B))}
    paths = {o.path for o in owners if o.name == PRIMARY}
    assert paths == {
        PRIMARY_PATH,
        "segments[Voice].dns.primary_provider.service",
    }


def test_a_css_provider_owns_its_row_and_an_empty_one_owns_none():
    module = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}}
            ),
            segment_entry(1, "Voice", SEG1, css={"provider": {"logicalId": ""}}),
            segment_entry(2, "Guest", SEG1, css={}),
        ],
        MISSING,
    )
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    css = [o for o in owners if o.name == CSS_PROVIDER]
    assert len(css) == 1
    assert css[0].value == UUID(CSS)
    assert (css[0].segment_id, css[0].segment_name) == (0, "Global Segment")
    assert css[0].identity_type is NetworkServiceIdentity
    assert css[0].path == 'segments["Global Segment"].css.provider.logical_id'


def test_a_provider_given_as_a_list_is_not_an_owner():
    module = ds_module(
        [
            segment_entry(
                0,
                "Global Segment",
                SEG0,
                dns={
                    **dns(),
                    "primaryProvider": [{"ref": PRIMARY}],
                },
            )
        ],
        global_segment_refs(**{PRIMARY: primary_row()}),
    )
    (listed,) = cast(list[DnsProvider], dns_of(module.data).primary_provider)
    assert type(listed) is DnsProvider and listed.ref == PRIMARY
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    assert [o.name for o in owners] == ["deviceSettings:segment"]
    assert ref_only_values(module.data) == {
        'segments["Global Segment"].dns.backup_provider.service': None
    }


def test_none_missing_and_empty_text_hold_no_identity():
    module = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_segment_refs(),
        refs_loaded=False,
    )
    # The profile's segment owner holds an identity; the unread provider (MISSING) holds none.
    owners = ref_owners(module.data, ConfigContext.PROFILE)
    assert [o.name for o in owners] == ["deviceSettings:segment"]


def test_ref_owners_normalises_uuid_text():
    module = primary_module()
    from_uuid = ref_owners(module.data, ConfigContext.PROFILE)
    # Assignment does not validate: a caller can write text.
    primary_of(module).service = DNS_A  # pyright: ignore[reportAttributeAccessIssue]
    from_text = ref_owners(module.data, ConfigContext.PROFILE)
    assert from_text == from_uuid
    assert owner_of(from_text, PRIMARY).value == UUID(DNS_A)
    assert isinstance(owner_of(from_text, PRIMARY).value, uuid.UUID)


def test_ref_owners_normalises_uuid_text_of_an_owns_ref_identity():
    module = ds_module(
        [
            segment_entry(
                0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}}
            )
        ],
        MISSING,
    )
    from_uuid = ref_owners(module.data, ConfigContext.PROFILE)
    css = module.data.segments[0].css
    assert css is not MISSING and css.provider is not MISSING
    css.provider.logical_id = str(CSS)  # pyright: ignore[reportAttributeAccessIssue]
    assert ref_owners(module.data, ConfigContext.PROFILE) == from_uuid


def test_ref_only_values_hold_every_field_reached():
    module = ds_module(
        [
            segment_entry(
                0,
                "Global Segment",
                SEG0,
                dns=dns(primary=True),
                authentication={"ref": AUTHENTICATION},
            )
        ],
        global_segment_refs(**{PRIMARY: primary_row()}),
        refs_loaded=False,
    )
    assert ref_only_values(module.data) == {
        'segments["Global Segment"].authentication.service': MISSING,
        'segments["Global Segment"].dns.primary_provider.service': MISSING,
        'segments["Global Segment"].dns.backup_provider.service': MISSING,
    }
    loaded = primary_module()
    assert ref_only_values(loaded.data) == {
        PRIMARY_PATH: UUID(DNS_A),
        PRIMARY_PATH.replace("primary_provider", "backup_provider"): None,
    }


def test_the_walk_takes_the_items_of_segments_and_no_other_list():
    # A provider sits in a list in the data of a DnsProvider-typed list field: no owner.
    module = ds_module(
        [
            segment_entry(
                0,
                "Global Segment",
                SEG0,
                dns={
                    **dns(),
                    "backupProvider": [{"ref": BACKUP}, {"ref": BACKUP}],
                },
            )
        ],
        MISSING,
    )
    assert ref_only_values(module.data).keys() == {
        PRIMARY_PATH,
    }


def test_segment_object_ids_come_from_the_segment_rows():
    refs: dict[str, Any] = {
        "deviceSettings:segment": [
            segment_row(0, SEG0_OBJECT, SEG0),
            segment_row(1, SEG1_OBJECT, SEG1),
            {"enterpriseObjectId": 9, "data": None},
        ]
    }
    assert segment_object_ids(refs) == {0: SEG0_OBJECT, 1: SEG1_OBJECT}
    assert segment_object_ids(
        {"deviceSettings:segment": refs["deviceSettings:segment"][0]}
    ) == {0: SEG0_OBJECT}
    assert segment_object_ids(MISSING) == {}
    assert segment_object_ids({}) == {}
    assert segment_object_ids({"deviceSettings:segment": "x"}) == {}


# --- the client ------------------------------------------------------------


async def test_get_configuration_modules_and_an_insert_answer_are_not_loaded(
    mock_vco_client: MockVcoClientFactory,
):
    store = store_with_services()
    store.add_configuration({"id": CONFIG, "name": "Profile"})
    ds = ds_module(
        [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
        global_segment_refs(**{PRIMARY: primary_row()}),
    )
    store.rows[DS_ID] = copy.deepcopy(
        ds.model_dump(mode="json", by_alias=True) | {"refs": ds.raw_refs}
    )
    client = await mock_vco_client(store)

    listed = await client.get_configuration_modules(
        GetConfigurationModules(configuration_id=CONFIG, no_data=False),
        layer=Profile,
    )
    (read,) = listed
    assert isinstance(read, DeviceSettingsConfigurationModule)
    assert read.refs_loaded is False
    assert primary_of(read.data).service is MISSING

    config = await client.get_configuration(
        GetConfiguration(id_=CONFIG, with_=[GetConfigurationWith.MODULES])
    )
    assert config.modules is not MISSING
    (loaded,) = config.modules
    assert isinstance(loaded, DeviceSettingsConfigurationModule)
    assert loaded.refs_loaded is True
    assert primary_of(loaded.data).service == UUID(DNS_A)

    inserted = await client.insert_configuration_module(
        InsertFirewallConfigurationModule(
            configuration_id=CONFIG, data={"segments": []}, return_data=True
        ),
        level=ConfigContext.PROFILE,
    )
    assert isinstance(inserted, FirewallConfigurationModule)
    assert inserted.refs_loaded is False

    updated = await client.update_configuration_module(
        UpdateConfigurationModule(
            configuration_module_id=DS_ID,
            version="v1",
            update=ConfigurationModuleUpdate(
                data=ds.data.model_dump(mode="json", by_alias=True)
            ),
            return_data=True,
        )
    )
    assert isinstance(updated, DeviceSettingsConfigurationModule)
    assert updated.refs_loaded is True


def test_a_module_list_validated_with_the_flag_is_not_loaded():
    row = primary_module().model_dump(mode="json", by_alias=True)
    parsed = ConfigurationModuleList[Profile].model_validate(
        [row], context={"refs_loaded": False}
    )
    assert parsed.root[0].refs_loaded is False


@pytest.mark.parametrize("loaded", [True, False])
def test_a_module_copy_keeps_whether_its_refs_were_read(loaded: bool):
    module = primary_module(refs_loaded=loaded)
    assert copy.deepcopy(module).refs_loaded is loaded


def test_only_the_classes_that_can_hold_an_owner_are_walked():
    assert can_hold_owner(DeviceSettingsData) is True
    assert can_hold_owner(DeviceSegment) is True
    assert can_hold_owner(SegmentDns) is True
    assert can_hold_owner(DnsPrimaryProvider) is True
    assert can_hold_owner(CssProviderRef) is True
    for data_class in (FirewallData, WanData, ControlPlaneData):
        assert can_hold_owner(data_class) is False
    assert can_hold_owner(QosData) is True  # the backhaul hub of a rule
    assert can_hold_owner(QosRule) is True
    assert can_hold_owner(BackhaulEdge) is True
    assert can_hold_owner(FirewallOutboundRule) is False
    assert can_hold_owner(DnsProvider) is False
    assert can_hold_owner(SegmentMetadata) is False


def test_the_data_of_a_module_that_holds_no_owner_has_none():
    row = make_module_json("firewall", {"segments": []}, configuration_id=CONFIG)
    module = FirewallConfigurationModule[Profile].model_validate(row)
    assert ref_owners(module.data, ConfigContext.PROFILE) == ()
    assert ref_only_values(module.data) == {}
    fill_ref_only(module.data, {})
