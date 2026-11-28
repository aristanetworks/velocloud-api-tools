"""A path pyvelo shows reads back as written, and ``apply`` takes it to its place.

One test per surface that shows a path: the plan's lines and the field changes,
a patch error, a dangling reference and its mending patches, the hints a message
shows, a warning, a ref line, a write error and its redo patch, a validator's
finding, an edit of the layer a save does not write, the effective comparison.
Each takes a path from the surface, checks that its text parses back to the same
parts, gives it to ``apply`` with the module the surface names, and checks that
the place reached is the one shown.

Then the names: every module name a surface holds is one ``apply`` takes, and
no text holds the VCO's name of a module or the key the VCO writes a field
under. Last, the refusals that name places only, made from a state that holds
a secret, show none of it.

The states are the ones each surface's own tests build.
"""

import copy
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from hub_helpers import HUB_A, HUB_B, backhaul_row, ds_with_vpn
from hub_state import (
    FROM_BOTH_LISTS,
    NEW_PASSWORD,
    NEW_PASSWORD_PATCH,
    OLD_PASSWORD,
    OUT_OF_THE_RULE,
    edit,
    edited,
    guest_backhaul_state,
    hub_backhaul_rule,
    hub_pipeline,
    rule_uses_hub,
    state_with_a_bgp_password,
)
from profile_state import module_store_pipeline, profile_state
from pydantic import MISSING
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    python_module_name,
)
from pyvelo.client.exceptions import (
    BackhaulWithoutHubError,
    ComposedModuleError,
    ConfigConflictError,
    DetachedObjectError,
    FixedListError,
    HubListError,
    LossyFieldError,
    ModuleWriteError,
    RefsDroppedError,
    RefsNotLoadedError,
    SegmentSetError,
    VersionRequiredError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.profile_segments import (
    AddedDefaults,
    AddedSegment,
    with_additions,
)
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    EnterpriseConfigurationProfile,
)
from pyvelo.config import (
    ConfigPipeline,
    ConfigState,
    DanglingReferenceError,
    LayerNotSavedError,
    PatchError,
)
from pyvelo.config.effective import EffectiveComparison
from pyvelo.config.errors import SaveStepError, SaveValidationError
from pyvelo.config.module_names import resolve_module_name
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.validation import ValidationError
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.config.validators.builtin import key_uniqueness, required_fields
from pyvelo.config.validators.device_settings import validate_no_overlapping_subnets
from pyvelo.config.validators.firewall import (
    validate_interface_firewall_consistency,
    validate_rule_names_unique,
)
from pyvelo.core import VcoModel, add, named_path
from pyvelo.core.base import field_models, wire_name
from pyvelo.core.identity import HubServiceNotFoundError
from pyvelo.core.layers import Profile
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import MockVcoClient, make_module_json
from refs_helpers import (
    CONFIG,
    CSS,
    DNS_A,
    DNS_B,
    DS_ID,
    PRIMARY,
    QOS_ID,
    SEG0,
    SEG0_OBJECT,
    SEGMENT,
    dns,
    segment_entry,
    segment_row,
    service_row,
    table_registry,
)
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from shown_paths import reached, round_trips
from test_apply_paths import (  # pyright: ignore[reportImplicitRelativeImport]
    _rule as qos_rule_body,  # pyright: ignore[reportPrivateUsage]  # the body of an added rule
)
from test_apply_paths import (  # pyright: ignore[reportImplicitRelativeImport]
    _twins as twins,  # pyright: ignore[reportPrivateUsage]  # the lists nothing tells apart
)
from test_backhaul_without_hub import (  # pyright: ignore[reportImplicitRelativeImport]
    out_of_rule,
)
from test_builtin_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _routes_state as routes_state,  # pyright: ignore[reportPrivateUsage]  # the device settings with static routes
)
from test_builtin_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _static_route as static_route,  # pyright: ignore[reportPrivateUsage]  # one route of those
)
from test_compare_effective import (  # pyright: ignore[reportImplicitRelativeImport]  # pyright: ignore[reportImplicitRelativeImport]
    EDGE_ID,
    OTHER_RULE,
    composed_modules,
    composed_qos,
    composed_segment,
    guest_qos,
    loaded,
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _firewall_module as firewall_module,  # pyright: ignore[reportPrivateUsage]  # a firewall with outbound rules
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _make_state as validated_state,  # pyright: ignore[reportPrivateUsage]  # a profile of the given modules
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _mod_kwargs as module_fields,  # pyright: ignore[reportPrivateUsage]  # a module's other fields
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _networks_state as networks_state,  # pyright: ignore[reportPrivateUsage]  # networks that overlap
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    _outbound_rule as outbound_rule,  # pyright: ignore[reportPrivateUsage]  # one rule of a firewall
)
from test_domain_validators import (  # pyright: ignore[reportImplicitRelativeImport]
    state_with_two_rules_named,
)
from test_hub_checks import (  # pyright: ignore[reportImplicitRelativeImport]
    add as add_hub,
)
from test_hub_checks import (  # pyright: ignore[reportImplicitRelativeImport]
    broken_as_read,
)
from test_hub_checks import (  # pyright: ignore[reportImplicitRelativeImport]
    edit as edit_vpn,
)
from test_hub_checks import (  # pyright: ignore[reportImplicitRelativeImport]
    profile_of as hub_list_profile,
)
from test_hub_in_use import (  # pyright: ignore[reportImplicitRelativeImport]
    used_hub_save,
)
from test_hub_removal_flow import (  # pyright: ignore[reportImplicitRelativeImport]
    refused as dangling,
)
from test_hub_removal_flow import (  # pyright: ignore[reportImplicitRelativeImport]
    send,
)
from test_hub_steps import (  # pyright: ignore[reportImplicitRelativeImport]
    profile_of as hub_role_profile,
)
from test_hub_steps import (  # pyright: ignore[reportImplicitRelativeImport]
    service_row as hub_role_row,
)
from test_pipeline_apply import (  # pyright: ignore[reportImplicitRelativeImport]
    _control_plane_state as control_plane_state,  # pyright: ignore[reportPrivateUsage]  # a state with a module a save does not write
)
from test_profile_segment_set import (  # pyright: ignore[reportImplicitRelativeImport]  # pyright: ignore[reportImplicitRelativeImport]
    edit_global_qos,
    in_all_three,
    two_defaults,
)
from test_profile_segment_set import (  # pyright: ignore[reportImplicitRelativeImport]
    state_of as segments_in,
)
from test_qos_ownership import (  # pyright: ignore[reportImplicitRelativeImport]
    flag_off_edge,
)
from test_qos_ownership import (  # pyright: ignore[reportImplicitRelativeImport]
    state_of as qos_edge_state,
)
from test_revert import (  # pyright: ignore[reportImplicitRelativeImport]
    drop_the_rules,
    lost_insert,
)
from test_save_refs import (  # pyright: ignore[reportImplicitRelativeImport]
    DNS_A_SERVICE,
    loaded_profile,
    primary_provider,
    ref_line_path,
)
from test_shown_plan import (  # pyright: ignore[reportImplicitRelativeImport]  # pyright: ignore[reportImplicitRelativeImport]
    RENAMED,
    ROUTE_HUBS,
    two_added_of_one_key,
)
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    make_state,
)
from test_state_references import (  # pyright: ignore[reportImplicitRelativeImport]
    SERVICE,
    SERVICE_ID,
    guest_rule_qos,
    hub_profile_ds,
    hub_profile_of,
    hub_refs,
    network,
)
from test_state_references import (  # pyright: ignore[reportImplicitRelativeImport]
    profile_of as vlan_profile,
)
from test_unconfirmed_insert import (  # pyright: ignore[reportImplicitRelativeImport]
    edge_qos_rows,
)
from tests.test_patch_fixed_list import (  # pyright: ignore[reportImplicitRelativeImport]
    given,
    without_alt_key,
)
from tests.test_patch_fixed_list import (  # pyright: ignore[reportImplicitRelativeImport]
    refusal as fixed_list_merge_refused,
)
from tests.test_patch_fixed_list import (  # pyright: ignore[reportImplicitRelativeImport]
    segment as fixed_segment,
)
from tests.test_write_module import (  # pyright: ignore[reportImplicitRelativeImport]
    firewall_disabled,
)
from tests.test_write_module import (  # pyright: ignore[reportImplicitRelativeImport]
    qos as qos_module,
)

apply = ConfigPipeline(client=None).apply

GUEST_AT = 1
"""The position of the segment ``Guest`` in each module of ``rule_uses_hub``."""
PORTS = {
    "segments": {
        "Guest": {
            "rules": {
                "Web": {
                    "match": {
                        "destination_port_low": 443,
                        "destination_port_high": 443,
                    }
                }
            }
        }
    }
}
HAND_LIST = (
    "backHaulEdges",
    "edgeToEdgeHub",
    "vpnHubs",
    "routeCfg",
    "edge2EdgeRouteAction",
    "dport_low",
    "cosMapping",
)
"""Keys the VCO writes fields of the fixtures under, which the derived set must hold."""


def data_of(state: ConfigState, module: str) -> VcoModel:
    """The data of the module of *state*'s target that *module* names."""
    data = state.module(module).data
    assert isinstance(data, VcoModel)
    return data


def parent(path: Path) -> Path:
    """*path* less its last part."""
    return Path(path.parts[:-1])


# --- the plan's lines and the field changes -------------------------------------


async def test_a_changed_value_of_the_plan_is_set_again_at_its_path() -> None:
    """A ``~`` line: its path, sent back with the new value, changes nothing."""
    state = await edited()
    before = state.get_field_changes()
    lines = state.plan().render().splitlines()
    both = [c for c in before if c.before is not MISSING and c.after is not MISSING]
    assert [str(c.path) for c in both] == [
        "segments[Guest].rules[Web].match.destination_port_low",
        "segments[Guest].vpn.hub_edges.enabled",
    ]
    for change in both:
        round_trips(change.path)
        assert any(line.startswith(f"  ~ {change.path}: ") for line in lines)
        assert reached(data_of(state, change.module), change.path) == change.after
        _ = await apply(
            state, module=change.module, path=str(change.path), patch=change.after
        )
        assert state.get_field_changes() == before


async def test_an_added_scalar_of_the_plan_is_set_again_at_its_path() -> None:
    """A ``+`` line of a scalar: sent back with its value, it changes nothing."""
    state = await edited()
    before = state.get_field_changes()
    (change,) = [c for c in before if c.before is MISSING]
    assert (change.module, change.path, change.after) == (
        "firewall",
        "firewall_logging_enabled",
        True,
    )
    round_trips(change.path)
    assert f"  + {change.path}: true" in state.plan().render().splitlines()
    _ = await apply(
        state, module=change.module, path=str(change.path), patch=change.after
    )
    assert state.get_field_changes() == before
    assert reached(data_of(state, change.module), change.path) is True


async def test_an_added_item_of_the_plan_is_reached_at_its_path() -> None:
    """A ``+`` line of an item: its path reaches the added entry, and no other."""
    state = await rule_uses_hub()
    hub = {"+": add({"name": HUB_B.name})}
    vpn = {"backHaulEdges": hub, "edgeToEdgeHub": {"vpnHubs": hub}}
    await edit(state, "device_settings", {"segments": {"Guest": {"vpn": vpn}}})
    before = state.get_field_changes()
    assert [(c.module, str(c.path)) for c in before] == [
        ("device_settings", "segments[Guest].vpn.hub_edges.vpn_hubs[Hub-B]"),
        ("device_settings", "segments[Guest].vpn.backhaul_edges[Hub-B]"),
    ]
    guest = state.profile.device_settings.segments[GUEST_AT].vpn
    added = (guest.hub_edges.vpn_hubs[1], guest.backhaul_edges[1])
    for change, entry in zip(before, added, strict=True):
        assert change.before is MISSING
        round_trips(change.path)
        _ = await apply(state, module=change.module, path=str(change.path), patch={})
        assert state.get_field_changes() == before
        assert reached(data_of(state, change.module), change.path) is entry
        assert entry.name == HUB_B.name


async def test_a_removed_item_of_the_plan_is_removed_again_at_its_path() -> None:
    """A ``-`` line: its path, with a delete, removes that item of the data as read.

    Every change of the plan is sent to one state as read, a removal as a
    delete and the others with their new values, since ``plan()`` judges the
    hub lists and the rule together: the state then has the same changes.
    """
    changes = (await edited()).get_field_changes()
    removed = [c for c in changes if c.after is MISSING]
    assert [(c.module, str(c.path)) for c in removed] == [
        ("business_policy", f"segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-A]"),
        ("device_settings", "segments[Guest].vpn.hub_edges.vpn_hubs[Hub-A]"),
        ("device_settings", "segments[Guest].vpn.backhaul_edges[Hub-A]"),
    ]
    fresh = await rule_uses_hub()
    for change in changes:
        round_trips(change.path)
        if change in removed:
            listed = reached(data_of(fresh, change.module), parent(change.path))
            assert listed == [reached(data_of(fresh, change.module), change.path)]
        _ = await apply(
            fresh,
            module=change.module,
            path=str(change.path),
            patch={"_op": "delete"} if change in removed else change.after,
        )
        if change in removed:
            assert reached(data_of(fresh, change.module), parent(change.path)) == []
    assert fresh.get_field_changes() == changes


async def test_an_item_removed_below_a_renamed_item_is_named_in_two_states() -> None:
    """Form only: the rule is named as edited and the hub below it as read.

    So the whole path names a place in neither state: the rule ``Web2`` is in
    the data as edited, which no longer has the hub, and the hub is in the
    data as read, where the rule is ``Web``.
    """
    state = await rule_uses_hub()
    await edit(state, "business_policy", OUT_OF_THE_RULE)
    await edit(state, "business_policy", RENAMED)
    (removal,) = [c for c in state.get_field_changes() if c.after is MISSING]
    assert removal.path == f"segments[Guest].rules[Web2].{ROUTE_HUBS}[Hub-A]"
    round_trips(removal.path)
    assert reached(data_of(state, removal.module), parent(removal.path)) == []


async def test_a_renamed_item_is_reached_by_its_new_name() -> None:
    """A change of a rule's name: its path names the rule as the state has it."""
    state = await rule_uses_hub()
    await edit(state, "business_policy", RENAMED)
    before = state.get_field_changes()
    (change,) = before
    assert (change.path, change.after) == ("segments[Guest].rules[Web2].name", "Web2")
    round_trips(change.path)
    _ = await apply(state, module=change.module, path=str(change.path), patch="Web2")
    assert state.get_field_changes() == before
    assert reached(data_of(state, change.module), change.path) == "Web2"


# --- errors ---------------------------------------------------------------------


async def unknown_field() -> PatchError:
    """The refusal of a patch that names a field the rule at position 0 lacks."""
    state = await rule_uses_hub()
    patch = {"segments": {"Guest": {"rules": {"#0": {"bogus": 1}}}}}
    with pytest.raises(PatchError) as error:
        _ = await apply(state, module="business_policy", patch=patch)
    return error.value


async def test_the_path_of_a_patch_error_reaches_the_item_it_names() -> None:
    """A patch error: its path less the refused field reaches the rule."""
    path = (await unknown_field()).path
    assert path == "segments[Guest].rules[Web].bogus"
    round_trips(path)
    fresh = await rule_uses_hub()
    rule = fresh.profile.qos.segments[GUEST_AT].rules[0]
    assert reached(fresh.profile.qos, parent(path)) is rule
    _ = await apply(
        fresh, module="business_policy", path=str(parent(path)), patch={"name": "Web"}
    )
    assert fresh.get_field_changes() == []


async def hub_removed() -> tuple[ConfigState, DanglingReferenceError]:
    """Hub-A out of Guest's hub lists while the rule ``Web`` backhauls to it."""
    state = await rule_uses_hub()
    await edit(state, "device_settings", FROM_BOTH_LISTS)
    return state, dangling(state)


async def test_the_path_of_a_dangling_reference_reaches_the_value() -> None:
    """A reference that names nothing: deleting the entry it is in mends the state."""
    state, error = await hub_removed()
    (reference,) = error.references
    assert reference.path == (
        f"segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-A].edge_logical_id"
    )
    round_trips(reference.path)
    data = data_of(state, reference.module)
    assert reached(data, reference.path) == reference.value
    _ = await apply(
        state,
        module=reference.module,
        path=str(parent(reference.path)),
        patch={"_op": "delete"},
    )
    _ = state.plan()  # no longer refused
    assert reached(data, parent(parent(reference.path))) == []


@pytest.mark.parametrize("kind", ["remove_reference", "add_member"])
async def test_the_mending_patches_of_one_kind_are_sent_as_given(kind: str) -> None:
    """A mending patch: its fields are ``apply``'s arguments, and it mends."""
    state, error = await hub_removed()
    chosen = [m for m in error.mending if m.kind == kind]
    assert chosen
    for mending in chosen:
        assert mending.path is None or Path.parse(mending.path) == mending.path
        await send(state, mending)
    _ = state.plan()  # no longer refused


# --- the patches a message shows ---------------------------------------------------

MODULE_AFTER = re.compile(
    r'(?: to |module=")(business_policy|device_settings|firewall)\b'
)


def shown_patches(text: str, module: str | None) -> list[tuple[str, dict[str, Any]]]:
    """Each patch *text* shows, with the module the text says to send it to.

    A patch is a JSON object in the text. The module is the one the text
    names between that patch and the next, or *module* where it names none
    there.
    """
    decoder = json.JSONDecoder()
    found: list[tuple[str, dict[str, Any]]] = []
    at = text.find('{"')
    while at != -1:
        patch, end = decoder.raw_decode(text, at)
        at = text.find('{"', end)
        named = MODULE_AFTER.search(text, end, len(text) if at == -1 else at)
        target = module if named is None else named.group(1)
        assert target is not None, f"no module for {patch}"
        found.append((target, patch))
    return found


def codes(state: ConfigState) -> list[str]:
    return [warning.code for warning in state.plan().warnings]


def hub_list_refused(state: ConfigState) -> HubListError:
    with pytest.raises(HubListError) as error:
        _ = state.plan()
    return error.value


async def hub_listed_with_the_list_off() -> ConfigState:
    """The Global Segment gets Hub-A in its Branch-to-Hub list, which is off."""
    state = await hub_list_profile(ds_with_vpn(GLOBAL))
    await edit_vpn(state, {"edgeToEdgeHub": {"vpnHubs": add_hub(HUB_A.name)}})
    return state


async def backhaul_not_a_hub() -> ConfigState:
    """Hub-A is a backhaul hub of the Global Segment and not a hub of it, as read."""
    state = await hub_list_profile(broken_as_read(), HUB_A, HUB_B)
    await edit_vpn(state, {"conditionalBackhaul": True})
    return state


async def missing_from_qos() -> ConfigState:
    """Guest is in the device settings and the firewall, and not in QoS."""
    return await segments_in(["Global", "Guest"], ["Global"], ["Global", "Guest"])


async def flag_off() -> ConfigState:
    """An edge that stores the class of service of Voice with its flag off."""
    return qos_edge_state(flag_off_edge())


async def rule_without_a_hub() -> ConfigState:
    """Guest's rule ``Web`` backhauls, and the edit takes its only hub out."""
    state = await guest_backhaul_state(hub_backhaul_rule("Web", HUB_A))
    await edit(state, "business_policy", out_of_rule("Web", HUB_A))
    return state


def hub_list_text(state: ConfigState) -> tuple[str, str | None]:
    return str(hub_list_refused(state)), None


def warning_text(code: str) -> Callable[[ConfigState], tuple[str, str | None]]:
    """The message of the one warning *code* of a state's plan, and its module."""

    def text(state: ConfigState) -> tuple[str, str | None]:
        (warning,) = [w for w in state.plan().warnings if w.code == code]
        return warning.message, warning.module

    return text


def no_hub_text(state: ConfigState) -> tuple[str, str | None]:
    with pytest.raises(BackhaulWithoutHubError) as error:
        _ = state.plan()
    (place,) = error.value.places
    return str(error.value), place.module


def plans_without(code: str) -> Callable[[ConfigState], bool]:
    return lambda state: code not in codes(state)


def plans(state: ConfigState) -> bool:
    _ = state.plan()
    return True


@pytest.mark.parametrize(
    ("make", "message", "count", "mended"),
    [
        (hub_listed_with_the_list_off, hub_list_text, 2, plans),
        (
            backhaul_not_a_hub,
            warning_text("hub_list_as_read"),
            2,
            plans_without("hub_list_as_read"),
        ),
        (
            missing_from_qos,
            warning_text("segments_disagree"),
            1,
            plans_without("segments_disagree"),
        ),
        (
            flag_off,
            warning_text("flag_off_in_use"),
            2,
            plans_without("flag_off_in_use"),
        ),
        (rule_without_a_hub, no_hub_text, 1, plans),
    ],
    ids=[
        "hub list refusal",
        "hub list as read",
        "segments disagree",
        "flag off in use",
        "backhaul without a hub",
    ],
)
async def test_a_patch_a_message_shows_is_sent_as_given_and_mends(
    make: Callable[[], Awaitable[ConfigState]],
    message: Callable[[ConfigState], tuple[str, str | None]],
    count: int,
    mended: Callable[[ConfigState], bool],
) -> None:
    """A hint: each patch of the message, sent alone to the module it names, mends.

    A patch that holds a place for the caller's own value (``<hub name>``) is
    not one to send as given: the backhaul refusal has two, which its own
    tests fill.
    """
    text, module = message(await make())
    shown = [
        (target, patch)
        for target, patch in shown_patches(text, module)
        if "<" not in json.dumps(patch)
    ]
    assert len(shown) == count
    for target, patch in shown:
        state = await make()
        _ = await apply(state, module=target, patch=patch)
        assert mended(state)


# --- warnings -------------------------------------------------------------------


def one_segment_profile(entry: dict[str, Any], **rows: object) -> ConfigState:
    """A profile whose device settings have the Global Segment *entry*.

    Its refs are the segment's row and *rows*.
    """
    data = {"lan": {"networks": [], "interfaces": []}, "segments": [entry]}
    row = make_module_json(
        "deviceSettings",
        data,
        module_id=DS_ID,
        configuration_id=CONFIG,
        refs={SEGMENT: segment_row(0, SEG0_OBJECT, SEG0), **rows},
    )
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": CONFIG, "modules": [row]}
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


def owner_without_a_row() -> ConfigState:
    """A profile whose Global Segment names a CSS provider that has no ref row."""
    return one_segment_profile(
        segment_entry(0, "Global Segment", SEG0, css={"provider": {"logicalId": CSS}})
    )


def two_rows_for_a_provider() -> ConfigState:
    """A profile whose Global Segment has two ref rows for its primary DNS provider."""
    rows = [
        service_row(PRIMARY, service, object_id, SEG0_OBJECT, SEG0)
        for service, object_id in ((DNS_A, 701), (DNS_B, 702))
    ]
    return one_segment_profile(
        segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True)),
        **{PRIMARY: rows},
    )


async def test_a_plan_warning_with_an_owner_is_at_the_owners_field() -> None:
    """A module plan's warning about a provider: its path reaches the provider."""
    state = owner_without_a_row()
    plan = state.plan()
    (module_plan,) = plan.modules
    (warning,) = [w for w in module_plan.warnings if w.code == "owner_without_ref"]
    assert warning.path == 'segments["Global Segment"].css.provider.logical_id'
    assert warning.segment is None  # the path names the segment
    round_trips(warning.path)
    assert reached(state.profile.device_settings, warning.path) == uuid.UUID(CSS)
    (lifted,) = [w for w in plan.warnings if w.code == "owner_without_ref"]
    assert (lifted.module, lifted.path) == ("device_settings", warning.path)


WARNING_CODES = {
    "backhaul_without_hub_kept",
    "hub_list_as_read",
    "hub_roles_disagree",
    "reference_kept",
    "flag_off_in_use",
    "segments_disagree",
    "ref_without_owner",
    "owner_without_ref",
    "several_refs",
    "defaults_repaired",
    "hub_in_use",
    "insert_unconfirmed",
}
"""The codes of the warnings ``warned`` gives: those these fixtures produce.

``defaults_not_template`` is not among them: only a plan of one module whose
``defaults`` differ from the template gives it, and its fixture is with the
tests of that repair.
"""


async def warned() -> list[tuple[ConfigState, ValidationWarning]]:
    """Each warning a plan or a save of the fixtures gives, with its state."""
    vlans = await vlan_profile(networks=[network(9, 100)])
    vlans.profile.device_settings.lan.networks[0].name = "renamed"
    hub_b = backhaul_row(
        HUB_B,
        service_id=SERVICE_ID + 1,
        service_logical_id=SERVICE,
        segment=GUEST,
        module_id=QOS_ID,
    )
    states = [
        await guest_backhaul_state(hub_backhaul_rule("Web")),
        # Guest's rule backhauls to Hub-B, and Hub-A alone is its backhaul hub.
        await hub_profile_of(
            hub_profile_ds(),
            guest_rule_qos(HUB_B),
            hub_refs(HUB_A, HUB_B, segment=GUEST),
            qos_refs={BACKHAUL_REF: hub_b},
        ),
        await backhaul_not_a_hub(),
        await hub_role_profile(
            [
                ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,)),
                ds_entry(GUEST),
            ],
            hub_role_row(HUB_A, 1, "hub"),
        ),
        vlans,
        await flag_off(),
        await missing_from_qos(),
        (await loaded_profile(css=True))[2],
        owner_without_a_row(),
        two_rows_for_a_provider(),
    ]
    found = [(state, warning) for state in states for warning in state.plan().warnings]

    # Three that a save gives: a hub in use, Guest's QoS defaults repaired, and
    # an insert whose answer was lost.
    pipeline, _, used = await used_hub_save()
    repaired = await profile_state(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST, defaults=two_defaults())],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
    )
    edit_global_qos(repaired)
    repairing, _ = module_store_pipeline(repaired)
    unanswered = make_state()
    inserting = await lost_insert(MockVcoClient.for_state(unanswered), unanswered)
    for saving, state in (
        (pipeline, used),
        (repairing, repaired),
        (inserting, unanswered),
    ):
        result = await saving.save(state, dry_run=True)
        found.extend((state, warning) for warning in result.warnings)
    return found


async def test_a_warning_of_a_plan_or_a_save_is_at_a_place_of_its_module() -> None:
    """A warning: its path reads back, and names a place of the module it names.

    The codes are those the fixtures produce (``WARNING_CODES`` says which
    code is not among them). A ref row that nothing in the data names is at
    its ref name under ``refs``, which is shown and is no place ``apply``
    takes, and a warning about a whole module has the empty path: form only.
    """
    found = await warned()
    assert {warning.code for _, warning in found} == WARNING_CODES
    hub_reference = f"segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-B].edge_logical_id"
    segments = {
        (warning.code, str(warning.path)): warning.segment for _, warning in found
    }
    assert segments[("reference_kept", hub_reference)] == "Guest"
    assert segments[("reference_kept", "lan.networks[renamed].segment_id")] is None
    row = 'refs."deviceSettings:css:provider"'
    assert segments[("ref_without_owner", row)] == "Global Segment"
    assert segments[("insert_unconfirmed", "")] is None
    for state, warning in found:
        path = warning.path
        assert isinstance(path, Path), warning.code
        round_trips(path)
        if not path.parts or path.parts[0] == "refs":
            assert warning.code in {"insert_unconfirmed", "ref_without_owner"}
            continue
        # A segment the three modules disagree about is named with no module:
        # the device settings have it.
        module = warning.module or "device_settings"
        assert reached(data_of(state, module), path) is not None, warning.code


async def test_a_providers_ref_line_names_the_field_that_sets_it() -> None:
    """A ref line of a provider: its path, given a service, sets that provider."""
    _, _, state = await loaded_profile()
    state.resolvers = await table_registry(DNS_A_SERVICE)
    primary_provider(state).service = uuid.UUID(DNS_A)
    (module_plan,) = state.plan().changed
    (line,) = module_plan.ref_lines()
    path = ref_line_path(line, "+", DNS_A)
    assert path == 'segments["Global Segment"].dns.primary_provider.service'
    round_trips(path)
    assert path in [change.path for change in state.get_field_changes()]

    pipeline, _, fresh = await loaded_profile()
    _ = await pipeline.apply(
        fresh, module="device_settings", path=str(path), patch=DNS_A
    )
    assert primary_provider(fresh).service == uuid.UUID(DNS_A)
    assert reached(fresh.profile.device_settings, path) == uuid.UUID(DNS_A)
    (again,) = fresh.plan().changed
    assert again.ref_lines() == [line]


# --- a write error ----------------------------------------------------------------


async def low_port_lost(*, both: bool = False) -> tuple[ConfigState, ModuleWriteError]:
    """A save of two ports of the rule ``Web`` whose low port the VCO does not store.

    The store keeps the low port as read, and with *both* the high port too.
    """
    state = await rule_uses_hub()
    await edit(state, "business_policy", PORTS)
    pipeline, store = hub_pipeline(state)

    def keep_as_read(new: dict[str, Any], _: dict[str, Any] | None) -> None:
        if new["name"] == "QOS":
            for segment in new["data"]["segments"]:
                for rule in segment["rules"]:
                    rule["match"]["dport_low"] = -1
                    if both:
                        rule["match"]["dport_high"] = -1

    store.on_store.append(keep_as_read)
    with pytest.raises(ModuleWriteError) as error:
        _ = await pipeline.save(state)
    return state, error.value


async def test_the_line_of_a_lost_edit_is_a_path_that_makes_the_edit_again() -> None:
    """A write error: the lost edit's path, with the value meant, is the edit again."""
    state, error = await low_port_lost()
    assert error.module_name == "business_policy"
    assert error.plan is not None
    (lost,) = error.lost_edits
    shown = error.plan.named(lost.path)
    assert shown == "segments[Guest].rules[Web].match.destination_port_low"
    assert f"  {shown}: read -1, meant 443, stored -1" in error.render().splitlines()
    round_trips(shown)
    assert shown not in [change.path for change in state.get_field_changes()]
    _ = await apply(
        state, module=error.module_name, path=str(shown), patch=lost.intended
    )
    (again,) = [c for c in state.get_field_changes() if c.path == shown]
    assert (again.module, again.before, again.after) == ("business_policy", -1, 443)


def planned_paths(state: ConfigState) -> list[Path]:
    """The path of each field change *state* plans."""
    return [change.path for change in state.get_field_changes()]


async def errors_with_lost_edits() -> list[tuple[ModuleWriteError, ConfigState]]:
    """The two write errors of ``write_errors`` a save raises, with the state.

    The state is the one the save left: it holds the module as stored, and
    plans none of the lost edits.
    """
    pairs = [await low_port_lost(), await low_port_lost(both=True)]
    return [(error, state) for state, error in pairs]


async def test_a_redo_patch_is_sent_as_given_and_redoes_the_lost_edits() -> None:
    """A redo patch, given to ``apply`` with the error's module, makes the lost edits.

    The plan then has a line at each lost edit's path as the error shows it,
    and no other, and each path reads back as written.
    """
    for error, state in await errors_with_lost_edits():
        assert error.plan is not None
        assert planned_paths(state) == []
        _ = await apply(state, module=error.module_name, patch=error.redo_patch())
        paths = planned_paths(state)
        assert sorted(paths) == sorted(
            error.plan.named(lost.path) for lost in error.lost_edits
        )
        for path in paths:
            round_trips(path)


# --- a validator's finding -----------------------------------------------------------


def unknown_interface_state() -> ConfigState:
    """A firewall rule ``ssh`` whose source interface the device settings lack.

    The state of the interface check's own test.
    """
    ds = DeviceSettingsData.model_validate(
        {
            "lan": {"networks": []},
            "segments": [ds_entry(GLOBAL)],
            "routedInterfaces": [{"name": "GE1"}],
        }
    )
    return validated_state(
        [
            DeviceSettingsConfigurationModule(data=ds, id_=2, **module_fields()),
            firewall_module(outbound_rule("web", "GE1"), outbound_rule("ssh", "GE9")),
        ]
    )


def validator_findings() -> list[tuple[ConfigState, ValidationError, object, str]]:
    """Each validator's finding over a state its own tests build, and its place.

    A static route twice, and one without its gateway; a QoS rule name twice,
    and a firewall one; two segments of overlapping networks; a firewall
    rule's unknown interface, which is a warning. With each, the object the
    finding is about: the second route, the gateway (``MISSING``), the second
    rule, the list of networks, the rule's match; and the text of the path,
    which names a segment by its name and an item by what names it alone (its
    name, else its key, else its position).
    """
    routes = routes_state(static_route("192.168.1.1"), static_route("192.168.1.1"))
    without_gateway = routes_state(
        static_route("192.168.1.1").model_copy(update={"gateway": MISSING})
    )
    qos_rules = state_with_two_rules_named("Web")
    firewall_rules = validated_state(
        [firewall_module(outbound_rule("same"), outbound_rule("same"))]
    )
    overlaps = networks_state(0, 7)
    interfaces = unknown_interface_state()

    segment_routes = routes.profile.device_settings.segments[0].routes
    assert segment_routes is not MISSING
    (duplicate_key,) = key_uniqueness.validate(routes).errors
    (missing,) = required_fields.validate(without_gateway).errors
    (duplicate_rule,) = validate_rule_names_unique.validate(qos_rules).errors
    (duplicate_firewall_rule,) = validate_rule_names_unique.validate(
        firewall_rules
    ).errors
    overlapping = validate_no_overlapping_subnets.validate(overlaps).errors
    assert len(overlapping) == 2
    (unknown,) = validate_interface_firewall_consistency.validate(interfaces).warnings
    second_rule = qos_rules.profile.qos.segments[1].rules[1]
    return [
        (
            routes,
            duplicate_key,
            segment_routes.static[1],
            'segments["Global Segment"].routes.static[#1]',
        ),
        (
            without_gateway,
            missing,
            MISSING,
            'segments["Global Segment"].routes.static[#0].gateway',
        ),
        (
            qos_rules,
            duplicate_rule,
            second_rule,
            f"segments[Guest].rules[{second_rule.rule_logical_id}]",
        ),
        (
            firewall_rules,
            duplicate_firewall_rule,
            firewall_rules.profile.firewall.segments[1].outbound[1],
            'segments["Global Segment"].outbound[#1]',
        ),
        *(
            (
                overlaps,
                finding,
                overlaps.profile.device_settings.lan.networks,
                "lan.networks",
            )
            for finding in overlapping
        ),
        (
            interfaces,
            unknown,
            interfaces.profile.firewall.segments[1].outbound[1].match,
            'segments["Global Segment"].outbound[ssh].match',
        ),
    ]


def test_the_path_of_a_validators_finding_reaches_what_it_is_about() -> None:
    """A finding's path reads as the plan shows a place, and reaches its object."""
    for state, finding, about, shown in validator_findings():
        assert str(finding.path) == shown
        round_trips(finding.path)
        assert finding.module is not None
        assert reached(data_of(state, finding.module), finding.path) is about


# --- the effective comparison ------------------------------------------------------


async def compared() -> EffectiveComparison:
    """An edge's firewall and QoS compared with a view that differs in each."""
    pipeline, state, client = await loaded(qos_profile=guest_qos())
    composed_segment(composed_qos(client), 2)["rules"][0]["ruleLogicalId"] = OTHER_RULE
    composed_modules(client)["syslog_forwarding"] = False
    return await pipeline.compare_effective(state)


async def test_a_path_of_the_effective_comparison_is_in_the_one_form() -> None:
    """Form only: fields by attribute, a segment by its name, an item by position.

    The comparison is of a view, which is no layer ``apply`` writes.
    """
    comparison = await compared()
    assert {entry.module for entry in comparison.entries} == {
        "business_policy",
        "firewall",
    }
    wire = wire_keys()
    segments: set[str] = set()
    for entry in comparison.entries:
        round_trips(entry.path)
        assert entry.source is None or Path.parse(entry.source) == entry.source
        fields = {part for part in entry.path.parts if isinstance(part, str)}
        assert not fields & wire, entry.path
        if entry.path.parts[0] == "segments":
            segment = entry.path.parts[1]
            assert isinstance(segment, Key)
            assert isinstance(segment.value, str)
            segments.add(segment.value)
    assert segments == {"Global Segment", "Guest"}
    assert "segments[Guest].rules[#0].rule_logical_id" in [
        entry.path for entry in comparison.findings
    ]


# --- items a bare name does not write -----------------------------------------------


async def test_a_composite_key_and_a_quoted_name_reach_their_item_and_no_other() -> (
    None
):
    """A syslog collector, a static route and rules whose names need quotes."""
    state = await rule_uses_hub()
    for host in ("10.0.0.1", "10.0.0.2"):
        _ = await apply(
            state,
            module="device_settings",
            segment="Guest",
            path="syslog.collectors",
            patch={"+": add({"host": host, "protocol": "UDP", "port": 514})},
        )
    for destination in ("10.0.0.0", "10.9.0.0"):
        route = (destination, "24", "192.168.1.1", "GE3")
        _ = await apply(
            state,
            module="device_settings",
            segment="Guest",
            path="routes.static",
            patch={route: add({"netmask": "255.255.255.0"})},
        )
    names = ("Corp.Guest v2", "a]b", 'say "x"')
    for name in names:
        _ = await apply(
            state,
            module="business_policy",
            segment="Guest",
            path="rules",
            patch={"+": add(qos_rule_body(name))},
        )
    guest = ("segments", GUEST_AT)
    data = state.profile.device_settings

    collector = named_path(Path((*guest, "syslog", "collectors", 0)), data)
    assert collector == 'segments[Guest].syslog.collectors["10.0.0.1",UDP,514]'
    round_trips(collector)
    _ = await apply(
        state, module="device_settings", path=str(collector), patch={"tag": "branch"}
    )
    assert reached(data, collector.child("tag")) == "branch"
    other = 'segments[Guest].syslog.collectors["10.0.0.2",UDP,514].tag'
    assert reached(data, Path.parse(other)) == ""

    route = named_path(Path((*guest, "routes", "static", 0)), data)
    assert route == 'segments[Guest].routes.static["10.0.0.0","24","192.168.1.1",GE3]'
    round_trips(route)
    _ = await apply(state, module="device_settings", path=str(route), patch={"cost": 5})
    assert reached(data, route.child("cost")) == 5
    other = 'segments[Guest].routes.static["10.9.0.0","24","192.168.1.1",GE3].cost'
    assert reached(data, Path.parse(other)) == 0

    rules = state.profile.qos.segments[GUEST_AT].rules
    for position, name in enumerate(names, start=1):
        rule = named_path(Path((*guest, "rules", position)), state.profile.qos)
        assert rule == f"segments[Guest].rules[{json.dumps(name)}]"
        round_trips(rule)
        _ = await apply(
            state,
            module="business_policy",
            path=str(rule.child("match")),
            patch={"destination_port_low": 8000 + position},
        )
        assert reached(state.profile.qos, rule) is rules[position]
    ports = [rule.match.destination_port_low for rule in rules]
    assert ports == [-1, 8001, 8002, 8003]


async def test_a_path_shown_by_position_reaches_the_item_at_that_position() -> None:
    """A ``+`` line of an item nothing names alone: ``[#n]`` is taken, and reaches it.

    Two networks added with one VLAN ID and one name: no other item has the
    text ``#0`` or ``#1`` as its key or name, so the position is not refused.
    """
    state = await two_added_of_one_key("p", "p")
    before = state.get_field_changes()
    networks = state.profile.device_settings.lan.networks
    shown = [c for c in before if isinstance(c.path.parts[-1], int)]
    assert [(str(c.path), c.before) for c in shown] == [
        ("lan.networks[#0]", MISSING),
        ("lan.networks[#1]", MISSING),
    ]
    for position, change in enumerate(shown):
        round_trips(change.path)
        assert reached(state.profile.device_settings, change.path) is networks[position]
        _ = await apply(state, module=change.module, path=str(change.path), patch={})
        assert state.get_field_changes() == before
    prefixes = [network.cidr_prefix for network in networks]
    _ = await apply(
        state,
        module="device_settings",
        path="lan.networks[#1]",
        patch={"cidr_prefix": 30},
    )
    assert prefixes[1] != 30
    assert [network.cidr_prefix for network in networks] == [
        prefixes[0],
        30,
        *prefixes[2:],
    ]


# --- limits: what a shown path does not do -------------------------------------------


async def test_limit_a_position_whose_text_names_another_item_is_refused() -> None:
    """An item nothing names alone is shown by its position, which may not be sent.

    The rule at position 1 has the logical ID of the next and the name
    ``dup`` of the next, so it is shown as ``[#1]``; the first rule is named
    ``#1``, and a patch key ``#1`` would reach that one. ``apply`` refuses
    the path and changes nothing.
    """
    state = await twins("#1")
    data = state.profile.qos
    shown = named_path(Path(("segments", GUEST_AT, "rules", 1, "match")), data)
    assert shown == "segments[Guest].rules[#1].match"
    round_trips(shown)
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            path=str(shown),
            patch={"destination_port_low": 443},
        )
    assert (error.value.kind, error.value.path) == (
        "address",
        "segments[Guest].rules[#1]",
    )
    assert 'write it in quotes: rules["#1"]' in error.value.message
    assert state.get_field_changes() == []


async def test_limit_a_quoted_position_text_is_a_name_before_it_is_a_position() -> None:
    """A hand-written ``["#1"]`` is the item of that key or name where one is.

    Only where the list has no such item is it the position, as the same
    text is in a patch key. pyvelo never writes a position in quotes.
    """
    state = await twins("#1")
    for text, port in (
        ("#1", 1001),  # the rule named ``#1``, at position 0
        ("#2", 1002),  # no rule is named ``#2``: position 2
    ):
        path = f'segments[Guest].rules["{text}"].match'
        assert Path.parse(path).parts[3] == Key(text)  # a name, not a position
        _ = await apply(
            state,
            module="business_policy",
            path=path,
            patch={"destination_port_low": port},
        )
    rules = state.profile.qos.segments[GUEST_AT].rules
    assert [rule.match.destination_port_low for rule in rules] == [1001, -1, 1002]


# --- the names: modules, and no key of the VCO's in any text --------------------------


# --- an edit of the layer a save does not write ---------------------------------


def take_hub_a_out(state: ConfigState) -> None:
    """Edit the profile of *state* in place: Hub-A out, and the port of ``Web``.

    Hub-A leaves Guest's two hub lists, the first of which is turned off, and
    the rule ``Web``, whose low destination port becomes 443. The profile is
    ``rule_uses_hub``'s.
    """
    vpn = state.profile.device_settings.segments[GUEST_AT].vpn
    vpn.hub_edges.enabled = False
    del vpn.hub_edges.vpn_hubs[0]
    del vpn.backhaul_edges[0]
    rule = state.profile.qos.segments[GUEST_AT].rules[0]
    rule.match.destination_port_low = 443
    hubs = rule.action.edge_to_edge_route_action.route_cfg.backhaul_edges
    assert isinstance(hubs, list)
    del hubs[0]


async def profile_layer_edited(
    profile: ConfigState,
    edit_profile: Callable[[ConfigState], None] = take_hub_a_out,
) -> LayerNotSavedError:
    """What ``plan()`` raises for an edge whose profile layer was edited.

    The edge is ``make_state``'s, over the profile of *profile*, a state of
    ``rule_uses_hub`` or one built as it is, which *edit_profile* edits.
    """
    state = ConfigState(
        edge_config=make_state().edge_config,
        profile_config=profile.profile_config,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
        resolvers=profile.resolvers,
    )
    state.take_snapshot()
    edit_profile(state)
    with pytest.raises(LayerNotSavedError) as raised:
        _ = state.plan()
    return raised.value


async def test_the_paths_of_an_unsaved_layer_make_its_edits_in_the_profile() -> None:
    """Each path of the error, sent to the profile with its module, makes the edit.

    The paths are the ones a plan of the profile shows for the same edits.
    """
    error = await profile_layer_edited(await rule_uses_hub())
    assert error.fields == {
        "device_settings": [
            "segments[Guest].vpn.hub_edges.enabled",
            "segments[Guest].vpn.hub_edges.vpn_hubs[Hub-A]",
            "segments[Guest].vpn.backhaul_edges[Hub-A]",
        ],
        "business_policy": [
            "segments[Guest].rules[Web].match.destination_port_low",
            f"segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-A]",
        ],
    }
    assert (
        "its profile layer was edited: device_settings "
        "(segments[Guest].vpn.hub_edges.enabled, "
        "segments[Guest].vpn.hub_edges.vpn_hubs[Hub-A], "
        "segments[Guest].vpn.backhaul_edges[Hub-A]); business_policy "
        "(segments[Guest].rules[Web].match.destination_port_low, "
        f"segments[Guest].rules[Web].{ROUTE_HUBS}[Hub-A]). "
    ) in str(error)

    planned = await rule_uses_hub()
    take_hub_a_out(planned)
    changes = planned.get_field_changes()
    assert {
        module: {str(c.path) for c in changes if c.module == module}
        for module in error.fields
    } == {module: set(paths) for module, paths in error.fields.items()}

    values = {
        "segments[Guest].vpn.hub_edges.enabled": False,
        "segments[Guest].rules[Web].match.destination_port_low": 443,
    }
    fresh = await rule_uses_hub()
    for module, paths in error.fields.items():
        for text in paths:
            path = Path.parse(text)
            round_trips(path)
            if text not in values:
                listed = reached(data_of(fresh, module), parent(path))
                assert listed == [reached(data_of(fresh, module), path)]
            patch = values.get(text, {"_op": "delete"})
            _ = await apply(fresh, module=module, path=text, patch=patch)
    assert fresh.get_field_changes() == changes


PORT_SHOWN = Path.parse("segments[Guest].rules[Web].match.destination_port_low")


async def write_errors() -> dict[str, Exception]:
    """A write error of each class, each of the business policy's two ports.

    The first two are raised by a save; the other two are made as the check
    of a write makes them, with the VCO's name of the module and that save's
    plan, whose changes are in the VCO's keys.
    """
    state, not_applied = await low_port_lost()
    _, conflict = await low_port_lost(both=True)
    assert isinstance(not_applied, WriteNotAppliedError)
    assert isinstance(conflict, ConfigConflictError)
    assert not_applied.plan is not None
    return {
        "write not applied": not_applied,
        "write conflict": conflict,
        "write outcome unknown": WriteOutcomeUnknownError(
            11,
            "v1",
            "no row",
            answer="counted",
            module_name="QOS",
            plan=not_applied.plan,
        ),
        "refs dropped": RefsDroppedError(
            11,
            "v1",
            module_name="QOS",
            plan=not_applied.plan,
            module=state.module("business_policy"),
            lost_edits=not_applied.lost_edits,
            refs_as_read={},
        ),
        "save step": SaveStepError("timeout", module_name="deviceSettings"),
    }


def segment_set_refused(state: ConfigState) -> SegmentSetError:
    """What ``plan()`` raises once the second segment of *state*'s QoS is deleted."""
    del state.profile.qos.segments[1]
    with pytest.raises(SegmentSetError) as segment_set:
        _ = state.plan()
    return segment_set.value


def segment_set_refused_by_one_module(state: ConfigState) -> SegmentSetError:
    """The same deletion, refused by a plan of the QoS module alone."""
    del state.profile.qos.segments[1]
    with pytest.raises(SegmentSetError) as segment_set:
        _ = plan_module(state.module("business_policy"))
    return segment_set.value


def hub_list_refused_by_one_module(state: ConfigState) -> HubListError:
    """Guest's first Branch-to-Hub hub deleted, refused by a plan of the module.

    *state* is ``rule_uses_hub``'s, or one built as it is.
    """
    del state.profile.device_settings.segments[GUEST_AT].vpn.hub_edges.vpn_hubs[0]
    with pytest.raises(HubListError) as hub_list:
        _ = plan_module(state.module("device_settings"))
    return hub_list.value


def fixed_list_refused(state: ConfigState) -> FixedListError:
    """Guest's first default QoS rule deleted, refused by ``plan()``.

    *state* is ``rule_uses_hub``'s, or one built as it is.
    """
    defaults = state.profile.qos.segments[GUEST_AT].defaults
    assert isinstance(defaults, list)
    del defaults[0]
    with pytest.raises(FixedListError) as fixed_list:
        _ = state.plan()
    return fixed_list.value


async def one_module_refusals() -> dict[str, Exception]:
    """The refusals of a plan of one module alone: a state's plan makes others."""
    return {
        "segment set, one module": segment_set_refused_by_one_module(
            await in_all_three()
        ),
        "hub list, one module": hub_list_refused_by_one_module(await rule_uses_hub()),
    }


async def refusals_below_a_state() -> dict[str, Exception]:
    """The refusals of a fixed list's merge, a segment addition and ``write_module``.

    Each is made as its own tests make it: the merge into a fixed list of test
    models, of a patch and of a list value that each change the alternate key;
    the addition of a segment to a module's data that holds it, and of
    defaults to one that lacks it; and the write of a firewall module with a
    plan of QoS.
    """
    patched = fixed_list_merge_refused(
        fixed_segment(), {"defaults": {"Beta": {"rule_id": str(uuid.uuid4())}}}
    )
    value = [without_alt_key(given(i)) for i in range(3)]
    value[1]["ruleId"] = str(uuid.uuid4())
    listed = fixed_list_merge_refused(fixed_segment(), {"defaults": value})
    entry = {"segment": {"segmentId": 2, "name": "Guest"}}
    with pytest.raises(ValueError, match="already") as added_twice:
        _ = with_additions(
            {"segments": [copy.deepcopy(entry)]}, (AddedSegment(2, "Guest", entry),)
        )
    with pytest.raises(ValueError, match="no segment") as no_segment:
        _ = with_additions({"segments": []}, (AddedDefaults(2, "Guest", ()),))
    plan = plan_module(qos_module({"segments": []}), {"segments": [], "x": 1})
    with pytest.raises(ValueError, match="the plan is for") as another_module:
        _ = await MockVcoClient().write_module(firewall_disabled(), plan)
    return {
        "fixed list, a patch": patched,
        "fixed list, a list value": listed,
        "segment added twice": added_twice.value,
        "defaults for a segment the data lacks": no_segment.value,
        "write_module of a plan for another module": another_module.value,
    }


async def not_written_refusals() -> dict[str, Exception]:
    """The refusals of a module a save does not write, and of a revert."""
    state = control_plane_state()
    with pytest.raises(PatchError) as patched:
        _ = await apply(
            state, module="control_plane", patch={"gateway_selection": {"mode": "x"}}
        )
    state.profile_config.control_plane.data.gateway_selection.mode = "static"
    with pytest.raises(TypeError) as saved:
        _ = await ConfigPipeline(MockVcoClient.for_state(state)).save(state)

    inserted = make_state()
    client = MockVcoClient.for_state(inserted)
    pipeline = await lost_insert(client, inserted)
    (stored_id,) = edge_qos_rows(client)
    client.store.other_writer(stored_id, drop_the_rules)
    with pytest.raises(ValueError, match="was not deleted") as reverted:
        _ = await pipeline.revert(inserted, module="business_policy")
    return {
        "patch to a module not written": patched.value,
        "save of a module not written": saved.value,
        "revert": reverted.value,
    }


def made_by_hand() -> dict[str, Exception]:
    """Errors a constructor makes here, each with the VCO's name of the module.

    The text of each names the module from that name. It prints the paths, or
    the message, as they are given, so what a raise site gives it is not
    covered by these four: they are given a path as pyvelo shows it.
    """
    return {
        "refs not loaded": RefsNotLoadedError("QOS", [PORT_SHOWN]),
        "lossy field": LossyFieldError("QOS", [PORT_SHOWN]),
        "version required": VersionRequiredError("QOS"),
        "hub service not found": HubServiceNotFoundError(
            hub="Hub-B", segment="Guest", path=PORT_SHOWN
        ),
        "detached object": DetachedObjectError(
            "the object at this path is no longer part of the state", [PORT_SHOWN]
        ),
    }


async def refusals() -> dict[str, Exception]:
    """An error of each class that names a module or a path, by what it refuses.

    Each is raised the way its own tests raise it, but those ``made_by_hand``
    and two of ``write_errors`` make.
    """
    pipeline, _, used = await used_hub_save()
    with pytest.raises(HubListError) as hub_in_use:
        _ = await pipeline.save(used, on_hub_in_use="refuse")
    with pytest.raises(BackhaulWithoutHubError) as no_hub:
        _ = (await rule_without_a_hub()).plan()
    with pytest.raises(PatchError) as position:
        _ = await apply(
            await twins("#1"),
            module="business_policy",
            path="segments[Guest].rules[#1].match",
            patch={},
        )
    with pytest.raises(ValueError, match="Unknown module") as unknown_module:
        _ = resolve_module_name("qos")
    comparing, compared_state, client = await loaded()
    client.store.composed[EDGE_ID]["QOS"] = None
    with pytest.raises(ComposedModuleError) as composed:
        _ = await comparing.compare_effective(compared_state)
    return {
        "patch": await unknown_field(),
        "position": position.value,
        "reference": (await hub_removed())[1],
        "backhaul without a hub": no_hub.value,
        "hub list": hub_list_refused(await hub_listed_with_the_list_off()),
        "hub in use": hub_in_use.value,
        "segment set": segment_set_refused(await in_all_three()),
        "fixed list": fixed_list_refused(await rule_uses_hub()),
        "composed module": composed.value,
        "module name": unknown_module.value,
        "layer not saved": await profile_layer_edited(await rule_uses_hub()),
        **await write_errors(),
        **await one_module_refusals(),
        **await refusals_below_a_state(),
        **await not_written_refusals(),
        **made_by_hand(),
    }


async def test_every_module_name_shown_is_one_apply_takes() -> None:
    """A module name of a field: it resolves, and it is the python name."""
    errors = await refusals()
    reference, no_hub, segment_set, write, step, layer = (
        errors["reference"],
        errors["backhaul without a hub"],
        errors["segment set"],
        errors["write not applied"],
        errors["save step"],
        errors["layer not saved"],
    )
    assert isinstance(reference, DanglingReferenceError)
    assert isinstance(no_hub, BackhaulWithoutHubError)
    assert isinstance(segment_set, SegmentSetError)
    assert isinstance(write, ModuleWriteError)
    assert isinstance(step, SaveStepError)
    assert isinstance(layer, LayerNotSavedError)
    names = [
        *(change.module for change in (await edited()).get_field_changes()),
        *(reference.module for reference in reference.references),
        *(mending.module for mending in reference.mending),
        *(place.module for place in no_hub.places),
        *segment_set.modules,
        write.module_name,
        step.module_name,
        *layer.fields,
        *(warning.module for _, warning in await warned()),
        *(entry.module for entry in (await compared()).entries),
    ]
    shown = {name for name in names if name is not None}
    assert shown == {"business_policy", "device_settings", "firewall"}
    for name in shown:
        resolved = resolve_module_name(name)
        assert python_module_name(resolved.value) == name


def reachable(*roots: type[VcoModel]) -> Iterator[type[VcoModel]]:
    """Each model class the data classes *roots* reach, once."""
    seen: set[type[VcoModel]] = set()
    todo = list(roots)
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        yield cls
        for field_info in cls.model_fields.values():
            todo.extend(field_models(field_info))


PROSE = frozenset({"QoS", "list", "type", "id"})
"""Keys that are also words a sentence of a message holds.

``QoS`` is the Orchestrator's word for the business policy ("the Edges'
QoS"), and a rule's action writes its ``qos`` field under it; ``list``,
``type`` and ``id`` are English. Each is still found where a path or a patch
writes it: only as a word of a sentence is it let be.
"""


def wire_keys() -> frozenset[str]:
    """The keys the VCO writes the fields of the three modules under.

    Every key of a field of a class the data of the device settings, QoS and
    firewall reaches that is not the field's attribute name. Left out is a
    key that is the attribute name of another field (``name``), which a path
    is right to hold.
    """
    keys: set[str] = set()
    attributes: set[str] = set()
    for cls in reachable(DeviceSettingsData, QosData[Profile], FirewallData[Profile]):
        for name in cls.model_fields:
            attributes.add(name)
            if wire_name(cls, name) != name:
                keys.add(wire_name(cls, name))
    return frozenset(keys - attributes)


VCO_MODULE_NAMES = frozenset(
    name.value
    for name in ConfigurationModuleName
    if python_module_name(name.value) != name.value
)
"""The VCO's name of each module that has another python name (``QOS``)."""
REF_NAMES = (
    "deviceSettings:backHaulEdge",
    "deviceSettings:css:provider",
    "deviceSettings:dns:primaryProvider",
    "deviceSettings:vnfs:edge",
)
"""The ref names the fixtures' texts hold: data, which is never renamed."""
REF_KEY = re.compile(
    r"\b(?:"
    + "|".join(map(re.escape, sorted(REF_NAMES, key=len, reverse=True)))
    + r")(?![\w.\[:])"
)
"""One of ``REF_NAMES``, whole.

Not a heading (``deviceSettings: update``) and not a module before a path:
nothing of a name, a path or a longer ref name follows it.
"""
VCO_FORMS = re.compile(
    re.escape("(the VCO's own name, such as QOS or deviceSettings, is accepted too)")
)
"""The sentence of an unknown module's error that names the VCO's forms on purpose."""


REQUEST_PARAMETERS = re.compile(
    r'\{"id": (?:\d+|<configuration ID>), "(?:with|_update)": '
    r'|\{"edgeId": <edge ID>\}'
)
"""The parameters of a request a message tells a caller to send to the VCO.

The start of the two forms the texts show (a configuration or a module by its
``id``, an edge by its ``edgeId``): the VCO is sent its own keys.
"""


def any_of(words: frozenset[str]) -> str:
    """A pattern for one of *words*, the longest that fits."""
    return "(?:" + "|".join(map(re.escape, sorted(words, key=len, reverse=True))) + ")"


def forbidden_names() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The patterns for a name of the VCO's where a text may not hold one.

    The first: a module name, and a key that is no word of a sentence
    (``PROSE``), as a whole word anywhere; and every key, those of ``PROSE``
    too, where it is written as a part of a path (after a dot, or first,
    before a dot or a bracket) or as the key of a JSON object. The second:
    every key as a path of one part, which is the first thing of a line of a
    plan or of an error, before a colon.
    """
    keys = wire_keys()
    word = any_of((keys - PROSE) | VCO_MODULE_NAMES)
    key = any_of(keys)
    written = re.compile(
        rf"(?<![A-Za-z0-9_]){word}(?![A-Za-z0-9_])"
        rf"|(?<=\.){key}(?![A-Za-z0-9_])"
        rf'|(?<![A-Za-z0-9_."]){key}(?=\.[A-Za-z_"]|\[)'
        rf'|(?<="){key}(?="\s*:)'
    )
    alone = re.compile(rf"^[ \t]*(?:[~+-] )?({key})(?=:)", re.MULTILINE)
    return written, alone


def vco_names(
    text: str, forbidden: tuple[re.Pattern[str], re.Pattern[str]]
) -> list[str]:
    """Each name of the VCO's that *text* holds outside what may hold one."""
    for allowed in (REF_KEY, VCO_FORMS, REQUEST_PARAMETERS):
        text = allowed.sub("", text)
    written, alone = forbidden
    return [*written.findall(text), *alone.findall(text)]


async def every_text() -> dict[str, str]:
    """Each text pyvelo writes over the fixtures, by the surface it is of."""
    texts = {"plan": (await edited()).plan().render()}
    added = await rule_uses_hub()
    hub = {"+": add({"name": HUB_B.name})}
    vpn = {"backhaul_edges": hub, "hub_edges": {"vpn_hubs": hub}}
    await edit(added, "device_settings", {"segments": {"Guest": {"vpn": vpn}}})
    texts["plan of an added hub"] = added.plan().render()
    _, _, provider = await loaded_profile()
    provider.resolvers = await table_registry(DNS_A_SERVICE)
    primary_provider(provider).service = uuid.UUID(DNS_A)
    texts["plan of a provider"] = provider.plan().render()
    for name, error in (await refusals()).items():
        texts[f"error: {name}"] = str(error)
        if isinstance(error, ModuleWriteError):
            texts[f"error: {name}, rendered"] = error.render()
            if error.lost_edits:
                texts[f"redo patch: {name}"] = json.dumps(error.redo_patch())
                texts[f"not redone: {name}"] = "\n".join(
                    str(change.path) for change in error.not_redone()
                )
    findings = [finding for _, finding, _, _ in validator_findings()]
    for position, finding in enumerate(findings):
        texts[f"finding {position}: {finding.code}"] = (
            f"{finding.module} {finding.path}: {finding.message}"
        )
    whole_module = ValidationError(
        path=Path(()), message="blocked", code="blocked", module="firewall"
    )
    texts["save blocked by validators"] = str(
        SaveValidationError([*findings, whole_module])
    )
    for position, (state, warning) in enumerate(await warned()):
        texts[f"warning {position}: {warning.code}"] = warning.message
        texts[f"plan with warning {position}"] = state.plan().render()
    texts["comparison"] = (await compared()).render()
    return texts


PLANTED = {
    "QOS: update": ["QOS"],
    "deviceSettings: update": ["deviceSettings"],
    "deviceSettings segments[Guest].vpn": ["deviceSettings"],
    "deviceSettings:segments[Guest]": ["deviceSettings"],
    "deviceSettings:QOS": ["deviceSettings", "QOS"],
    "(it writes device settings, QoS, WAN and firewall modules)": ["WAN"],
    "  ~ vpn.backHaulEdges[Hub-A]": ["backHaulEdges"],
    '{"match": {"dport_low": 443}}': ["dport_low"],
    "  ~ segments[Guest].rules[Web].match.sip: any -> 10.0.0.1": ["sip"],
    '{"match": {"sip": "any", "dip": "any"}}': ["sip", "dip"],
    "segments[Guest].rules[Web].action.QoS.type_": ["QoS"],
    '{"route_cfg": {"type": "edge"}}': ["type"],
    "  + list[#0]: 1": ["list"],
    "  ~ vpn.id: 1 -> 2": ["id"],
    "  ~ type: a -> b": ["type"],
    'send it with {"id": 11, "match": 1}': ["id"],
    # What may hold one: a ref name, and a word of a sentence.
    'refs."deviceSettings:backHaulEdge"': [],
    "- refs deviceSettings:backHaulEdge (Guest)": [],
    "the Edges' QoS": [],
    "change it in a patch of the whole list: end the path at rules": [],
    'with {"id": 100, "with": ["modules"]} or {"edgeId": <edge ID>}': [],
    "remove it from the backhaul list. A hub of that type has an id.": [],
    '"type_": "edgeHub"': [],
}
"""Texts with a name of the VCO's planted in them, and what the sweep finds in each."""


async def test_no_text_holds_a_vco_module_name_or_a_wire_key() -> None:
    """Every text names a module and a field as python does.

    The texts: three plans, an error of each class ``refusals`` makes (the
    refusals of a patch, a reference, the hub lists, the segment set, a fixed
    list in a plan and in a merge, a segment addition to a module's data,
    ``write_module`` given another module's plan, a composed module, a
    revert, a module a save does not write, an edit of the layer a save does
    not write, and every class of write error), the redo patch and what is
    not redone of each write error that lost edits, each warning of
    ``warned`` with the plan of its state, the comparison, and each
    validator's finding of ``validator_findings`` with its module and path,
    and the save they block.

    What may hold a name of the VCO's: a ref name, which is data, the one
    sentence that tells a caller the VCO's module names are accepted, the
    parameters of a request to the VCO that a message shows, and a key that
    is a word, in a sentence (``PROSE``).

    Not covered: the paths and messages that the raise sites of the errors of
    ``made_by_hand`` give them; the ``defaults_not_template`` warning; the two
    refusals of a patch that ``_resolve_items`` of ``core/merge.py`` builds
    (``... names this item too``, ``... adds this key too``); the
    ``source`` of a ``Resolved`` value of ``state.effective``, a wire path in
    the layer the value came from; the path of a
    ``DetachedObjectError`` that starts at the object an edit was made
    through, such as a row, and not at a module's data; and the errors that
    print what the VCO answered (``VcoApiError``, ``VcoOperationError``). A
    redo patch that writes an object, adds or moves an item, or names a hub, a
    segment or a provider, and the ``refs`` lines of a write error, are read
    in ``test_write_error_names.py``; here a redo patch is one scalar edit.
    """
    wire = wire_keys()
    assert set(HAND_LIST) <= wire
    assert wire >= PROSE
    assert len(wire) > 400
    assert {"QOS", "deviceSettings", "WAN", "controlPlane"} <= VCO_MODULE_NAMES
    forbidden = forbidden_names()
    for text, names in PLANTED.items():
        assert vco_names(text, forbidden) == names, text

    texts = await every_text()
    assert len(texts) > 60
    assert texts["not redone: refs dropped"]
    assert {
        "redo patch: write not applied",
        "redo patch: write conflict",
        "finding 0: key_uniqueness",
        "finding 6: unknown_interface_ref",
        "save blocked by validators",
        "error: fixed list, a patch",
        "error: segment added twice",
        "error: write_module of a plan for another module",
    } <= set(texts)
    assert {
        key.partition(": ")[2] for key in texts if key.startswith("warning ")
    } == WARNING_CODES
    found = {
        surface: names
        for surface, text in texts.items()
        if (names := vco_names(text, forbidden))
    }
    assert found == {}
    # The exceptions are of the fixtures: a ref name is shown, and so is the sentence.
    assert REF_KEY.search(texts["plan"]) is not None
    assert VCO_FORMS.search(texts["error: module name"]) is not None


# --- a secret in a state whose refusals name places ---------------------------------


async def a_new_password() -> ConfigState:
    """``state_with_a_bgp_password`` of ``OLD_PASSWORD``; an edit sets ``NEW``."""
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    await edit(state, "device_settings", NEW_PASSWORD_PATCH)
    return state


def take_hub_a_out_and_change_the_password(state: ConfigState) -> None:
    """``take_hub_a_out``, and ``NEW_PASSWORD`` for the profile's BGP neighbour."""
    take_hub_a_out(state)
    _ = state.profile.device_settings.merge_patch(NEW_PASSWORD_PATCH)


async def test_a_refusal_that_names_places_holds_no_secret_of_its_state() -> None:
    """A refusal of paths and names, made over a state that holds a secret.

    The refusals ``refusals`` makes from ``rule_uses_hub``, made again as it
    makes them from that state with a BGP password in its Global Segment,
    set anew by an edit (the unsaved profile layer's, by an edit of that
    layer, which the error names): neither password is in the text.
    """
    errors: dict[str, Exception] = {
        "fixed list": fixed_list_refused(await a_new_password()),
        "hub list, one module": hub_list_refused_by_one_module(await a_new_password()),
        "segment set": segment_set_refused(await a_new_password()),
        "segment set, one module": segment_set_refused_by_one_module(
            await a_new_password()
        ),
        "layer not saved": await profile_layer_edited(
            await state_with_a_bgp_password(OLD_PASSWORD),
            take_hub_a_out_and_change_the_password,
        ),
    }
    for name, error in errors.items():
        text = str(error)
        assert OLD_PASSWORD not in text, name
        assert NEW_PASSWORD not in text, name
    layer = errors["layer not saved"]
    assert isinstance(layer, LayerNotSavedError)
    assert (
        'segments["Global Segment"].bgp.neighbors["192.0.2.1"].md5_password'
        in layer.fields["device_settings"]
    )
