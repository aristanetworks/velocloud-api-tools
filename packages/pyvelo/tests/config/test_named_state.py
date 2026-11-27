"""A configuration read back from its named dump sends what its wire dump would.

A configuration's ``named_dump`` holds each row of ``modules`` as that row's
own named dump: attribute names, every secret masked. ``named_validate`` reads
the document back row by row, each by the class its ``name`` has, with the
row of that name in ``stored`` giving the masks their values. A module row
keeps the JSON it was validated from, and a save starts from that, so the
rows read back must keep it in the VCO's keys with the complete secrets: here
a state rebuilt through the named pair plans and sends, byte for byte, what a
state rebuilt from the wire dump does.

``test_named_rows.py`` takes the rows one by one, unedited and under edits.
"""

import copy
import json
import traceback
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

import pytest
from hub_state import (
    DS,
    FROM_BOTH_LISTS,
    NEIGHBOR_IP,
    NEW_PASSWORD_PATCH,
    OUT_OF_THE_RULE,
    PORT,
    QOS,
    edge_over_a_hub_profile,
    hub_pipeline,
    rule_uses_hub,
    state_with_a_bgp_password,
)
from hub_state import NEW_PASSWORD as NEW
from hub_state import OLD_PASSWORD as OLD
from pydantic import MISSING
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    ConfigurationBase,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
    row_classes,
)
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.core import HIDDEN, ConfigContext, NamedDocumentError
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing import MockVcoClient, ModuleStore
from test_named_rows import (  # pyright: ignore[reportImplicitRelativeImport]
    named_of,
    wire_of,
)
from test_state_plan import make_state  # pyright: ignore[reportImplicitRelativeImport]

type Read = Callable[[], Awaitable[ConfigState]]
"""A state as read."""
type Serve = Callable[[ConfigState], tuple[ConfigPipeline, ModuleStore]]
"""A pipeline over a new store that holds a state as read."""
type Config = EdgeConfiguration | EnterpriseConfigurationProfile


def _again[C: ConfigurationBase[Any]](config: C, *, named: bool) -> C:
    """*config* validated from the JSON text of its dump, at the level it has."""
    if named:
        dumped = json.loads(json.dumps(config.named_dump()))
        again = type(config).named_validate(dumped, stored=config)
    else:
        dumped = json.loads(json.dumps(config.model_dump(mode="json", by_alias=True)))
        again = type(config).model_validate(dumped)
    if config.config_context is ConfigContext.PROFILE:
        return again.for_profile()
    return again


def rebuilt(state: ConfigState, *, named: bool) -> ConfigState:
    """*state* with each configuration validated from its own dump.

    The named dump, read back by ``named_validate``, or the wire dump.
    """
    edge = state.edge_config
    again = ConfigState(
        edge_config=None if edge is None else _again(edge, named=named),
        profile_config=_again(state.profile_config, named=named),
        target_type=state.target_type,
        resolver_context=state.resolver_context,
        resolvers=state.resolvers,
    )
    again.take_snapshot()
    return again


async def _edge_firewall() -> ConfigState:
    """An edge whose firewall lacks the profile's segment ``Guest``."""
    return make_state()


def serve(state: ConfigState) -> tuple[ConfigPipeline, ModuleStore]:
    """A pipeline over a new store that holds *state*, its hubs and their services."""
    return hub_pipeline(state, vpn_check=True)


def _over_the_state(state: ConfigState) -> tuple[ConfigPipeline, ModuleStore]:
    client = MockVcoClient.for_state(state)
    return ConfigPipeline(client), client.store


LOGGING: Final[dict[str, Any]] = {"firewall_logging_enabled": True}
SYSLOG: Final[dict[str, Any]] = {
    "segments": {"Global Segment": {"syslog": {"enabled": True}}}
}

type Edits = tuple[tuple[str, dict[str, Any]], ...]
"""Patches, each with the module it is sent to."""

SCENARIOS: Final[dict[str, tuple[Read, Serve, Edits]]] = {
    "profile firewall": (rule_uses_hub, serve, (("firewall", LOGGING),)),
    "profile QoS": (rule_uses_hub, serve, ((QOS, PORT),)),
    "profile device settings and its refs": (
        rule_uses_hub,
        serve,
        ((DS, FROM_BOTH_LISTS), (QOS, OUT_OF_THE_RULE)),
    ),
    "edge firewall": (_edge_firewall, _over_the_state, (("firewall", LOGGING),)),
    "edge QoS": (edge_over_a_hub_profile, serve, ((QOS, PORT),)),
    "edge device settings": (edge_over_a_hub_profile, serve, ((DS, SYSLOG),)),
}


async def _planned_and_sent(
    scenario: str, read: ConfigState, *, named: bool
) -> tuple[str, str]:
    """The plan text and the requests of the scenario's edits, saved.

    The state is *read* rebuilt from its dump; a new store holds *read*.
    """
    _, over, edits = SCENARIOS[scenario]
    pipeline, store = over(read)
    state = rebuilt(read, named=named)
    for module, patch in edits:
        _ = await pipeline.apply(state, module=module, patch=patch)
    plan = state.plan().render()
    since = len(store.requests)
    _ = await pipeline.save(state)
    writes = [
        [method, params]
        for method, params in store.requests[since:]
        if not method.split("/")[-1].startswith("get")
    ]
    assert writes, scenario
    return plan, json.dumps(writes, indent=1)


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_a_state_from_its_named_dump_plans_and_sends_what_the_wire_dump_does(
    scenario: str,
) -> None:
    read = await SCENARIOS[scenario][0]()
    plan, sent = await _planned_and_sent(scenario, read, named=False)
    named_plan, named_sent = await _planned_and_sent(scenario, read, named=True)

    assert named_sent == sent
    assert named_plan == plan


async def test_the_edge_firewall_plan_reconciles_the_segment_it_lacks() -> None:
    read = await _edge_firewall()
    plan, _ = await _planned_and_sent("edge firewall", read, named=True)

    assert "reconciled segments: Guest" in plan


async def test_a_device_settings_write_carries_its_refs_by_the_vcos_keys() -> None:
    scenario = "profile device settings and its refs"
    read = await rule_uses_hub()
    _, sent = await _planned_and_sent(scenario, read, named=True)

    refs = [
        params["_update"]["refs"]
        for method, params in json.loads(sent)
        if method == "configuration/updateConfigurationModule"
        and "refs" in params["_update"]
    ]
    held = [rows for rows in refs if "deviceSettings:segment" in rows]
    assert held
    for rows in held:
        row = rows["deviceSettings:segment"][0]
        assert {"configurationId", "moduleId", "enterpriseObjectId"} <= set(row)
        assert not {"segment", "configuration_id", "module_id"} & (set(rows) | set(row))
        assert HIDDEN not in json.dumps(rows)


@pytest.mark.parametrize("read", [rule_uses_hub, edge_over_a_hub_profile, make_state])
async def test_each_module_keeps_the_json_the_vcos_keys_give(
    read: Read | Callable[[], ConfigState],
) -> None:
    made = read()
    state = made if isinstance(made, ConfigState) else await made
    wire = rebuilt(state, named=False)
    named = rebuilt(state, named=True)

    for by_keys, by_name in (
        (wire.edge_config, named.edge_config),
        (wire.profile_config, named.profile_config),
    ):
        if by_keys is None or by_name is None:
            continue
        assert by_keys.modules is not MISSING
        assert by_name.modules is not MISSING
        for kept, again in zip(by_keys.modules, by_name.modules, strict=True):
            assert json.dumps(again.raw_data) == json.dumps(kept.raw_data), kept.name
            if kept.raw_refs is MISSING:
                assert again.raw_refs is MISSING
            else:
                assert json.dumps(again.raw_refs) == json.dumps(kept.raw_refs), (
                    kept.name
                )


async def test_a_configuration_dumps_each_row_by_its_own_class() -> None:
    config = (await rule_uses_hub()).profile_config
    named: dict[str, Any] = config.named_dump()
    assert named == config.model_dump(mode="json", by_alias=False) | {
        "modules": [row.named_dump() for row in rows_of(config)]
    }
    assert {"configuration_id", "id_", "type_"} <= set(named["modules"][0])


def test_the_row_classes_of_a_configuration_are_those_of_its_layer() -> None:
    assert row_classes(EdgeConfiguration)["QOS"] is QosConfigurationModule[Edge]
    assert (
        row_classes(EnterpriseConfigurationProfile)["firewall"]
        is (FirewallConfigurationModule[Profile])
    )
    assert set(row_classes(EdgeConfiguration)) == {
        "controlPlane",
        "deviceSettings",
        "WAN",
        "QOS",
        "firewall",
        "analyticsSettings",
        "atpMetadata",
    }


async def test_a_secret_is_not_in_the_named_dump_and_is_sent_as_stored() -> None:
    read = await state_with_a_bgp_password(OLD)
    config = read.profile_config
    assert OLD not in json.dumps(config.named_dump())
    pipeline, store = serve(read)
    state = rebuilt(read, named=True)
    _ = await pipeline.apply(state, module="device_settings", patch=SYSLOG)
    _ = await pipeline.save(state)
    sent = json.dumps(store.requests)
    assert OLD in sent
    assert '"***"' not in sent


async def test_the_state_with_a_password_plans_nothing_and_takes_a_new_one() -> None:
    state = await state_with_a_bgp_password(OLD)
    assert state.plan().render().splitlines()[1:] == [
        "device_settings: none",
        "business_policy: none",
        "firewall: none",
    ]
    pipeline, store = serve(state)
    _ = await pipeline.apply(state, module="device_settings", patch=NEW_PASSWORD_PATCH)
    (change,) = state.get_field_changes()
    assert change.path == (
        f'segments["Global Segment"].bgp.neighbors["{NEIGHBOR_IP}"].md5_password'
    )
    assert (change.before, change.after) == (OLD, NEW)
    _ = await pipeline.save(state)
    sent = json.dumps(store.requests)
    assert NEW in sent
    assert OLD not in sent


# --- The pair on a configuration: the laws, and what a document may hold -----


async def every_fixture_configuration() -> list[Config]:
    """The configurations of four states, one of them with a password."""
    states = (
        await rule_uses_hub(),
        make_state(),
        await edge_over_a_hub_profile(),
        await state_with_a_bgp_password(OLD),
    )
    return [
        config
        for state in states
        for config in (state.edge_config, state.profile_config)
        if config is not None
    ]


def rows_of(config: Config) -> list[AnyStoredModule]:
    assert config.modules is not MISSING
    return list(config.modules)


def document_of(
    config: ConfigurationBase[Any], named: Mapping[str, object]
) -> dict[str, Any]:
    """``vco_document`` of *named*, with *config* as the stored configuration."""
    return type(config).vco_document(named, stored=config)


def read_back[C: ConfigurationBase[Any]](
    config: C, named: Mapping[str, object], context: dict[str, object] | None = None
) -> C:
    """``named_validate`` of *named*, with *config* as the stored configuration."""
    return type(config).named_validate(named, stored=config, context=context)


async def with_a_password() -> tuple[EnterpriseConfigurationProfile, dict[str, Any]]:
    """A profile whose device settings hold ``OLD``, and its named dump."""
    config = (await state_with_a_bgp_password(OLD)).profile_config
    return config, named_of(config)


def neighbor_of(row: dict[str, Any]) -> dict[str, Any]:
    """The BGP neighbour in a named device settings row."""
    (neighbor,) = row["data"]["segments"][0]["bgp"]["neighbors"]
    return neighbor


def password_sent_by(config: Config) -> object:
    """The neighbour's password in what a save of *config* starts from."""
    raw: Any = config.device_settings.raw_data
    (neighbor,) = raw["segments"][0]["bgp"]["neighbors"]
    return neighbor["md5Password"]


async def test_the_fixture_configurations_are_of_both_classes_and_hold_a_mask() -> None:
    configs = await every_fixture_configuration()
    assert sorted(type(config).__name__ for config in configs) == [
        "EdgeConfiguration",
        "EdgeConfiguration",
        "EnterpriseConfigurationProfile",
        "EnterpriseConfigurationProfile",
        "EnterpriseConfigurationProfile",
        "EnterpriseConfigurationProfile",
    ]
    assert sum(HIDDEN in json.dumps(named_of(config)) for config in configs) == 3


async def test_the_document_of_a_named_dump_is_the_wire_dump_key_for_key() -> None:
    for config in await every_fixture_configuration():
        document = document_of(config, named_of(config))
        assert json.dumps(document) == json.dumps(wire_of(config))


async def test_a_named_dump_shows_no_row_in_the_vcos_keys() -> None:
    for config in await every_fixture_configuration():
        named = named_of(config)
        assert [row["name"] for row in named["modules"]] == [
            row.name for row in rows_of(config)
        ]
        for row in named["modules"]:
            assert not {"configurationId", "schemaVersion"} & set(row)
            for entry in row["data"]["segments"]:
                assert "segment_id" in entry["segment"]


CONTEXTS: list[dict[str, object] | None] = [
    None,
    {"refs_loaded": False},
    {"config_level": ConfigContext.EDGE},
    {"config_level": ConfigContext.PROFILE, "refs_loaded": False},
]


@pytest.mark.parametrize("context", CONTEXTS)
async def test_a_configuration_read_back_is_the_one_its_wire_dump_gives(
    context: dict[str, object] | None,
) -> None:
    for config in await every_fixture_configuration():
        by_keys = type(config).model_validate(wire_of(config), context=context)
        by_name = read_back(config, named_of(config), context)
        assert json.dumps(wire_of(by_name)) == json.dumps(wire_of(by_keys))
        for kept, again in zip(rows_of(by_keys), rows_of(by_name), strict=True):
            assert type(again) is type(kept)
            assert again.refs_loaded is kept.refs_loaded
            assert again.config_context is kept.config_context
            assert json.dumps(again.raw_data) == json.dumps(kept.raw_data)


async def test_the_context_reaches_each_row() -> None:
    config, named = await with_a_password()
    plain = read_back(config, named)
    assert [row.refs_loaded for row in rows_of(plain)] == [True, True, True]
    unloaded = read_back(config, named, {"refs_loaded": False})
    assert [row.refs_loaded for row in rows_of(unloaded)] == [False, False, False]


async def test_an_edit_of_a_row_is_read_and_its_mask_keeps_the_stored_secret() -> None:
    config, named = await with_a_password()
    (row, *_) = named["modules"]
    neighbor = neighbor_of(row)
    assert neighbor["md5_password"] == HIDDEN
    neighbor["neighbor_as"] = "65010"
    again = read_back(config, named)
    raw: Any = again.device_settings.raw_data
    assert raw["segments"][0]["bgp"]["neighbors"][0]["neighborAS"] == "65010"
    assert password_sent_by(again) == OLD


async def test_a_row_is_found_by_its_name_wherever_it_is_in_the_document() -> None:
    config, named = await with_a_password()
    named["modules"].reverse()
    again = read_back(config, named)
    assert [row.name for row in rows_of(again)] == ["firewall", "QOS", "deviceSettings"]
    assert password_sent_by(again) == OLD
    document = document_of(config, named)
    wire = wire_of(config)
    assert json.dumps(document["modules"]) == json.dumps(wire["modules"][::-1])


async def test_a_document_without_modules_is_read_as_one() -> None:
    config, named = await with_a_password()
    del named["modules"]
    again = read_back(config, named)
    assert again.modules is MISSING
    assert again.id_ == config.id_
    assert "modules" not in document_of(config, named)


async def test_the_document_shares_nothing_with_what_it_is_made_from() -> None:
    config, named = await with_a_password()
    before = copy.deepcopy(named)
    wire = wire_of(config)
    document = document_of(config, named)
    document["modules"][0]["data"]["segments"].clear()
    document["modules"].clear()
    assert named == before
    assert wire_of(config) == wire


# --- A mask in a row that has no stored row ----------------------------------

NO_PLACE: Final = (
    'holds the mask "***", and there is no stored value to keep in its place '
    "({why}). The mask would be written to the VCO as the secret. Give the model "
    "this document was dumped from as `stored`, or write the secret in full."
)
PASSWORD: Final = "modules[#{row}].data.segments[#0].bgp.neighbors[#0].md5_password"
"""Where the neighbour's password is in a document, in its row at ``row``."""


def refusal_of[C: ConfigurationBase[Any]](
    config: type[C], named: dict[str, Any], stored: C | None
) -> NamedDocumentError:
    """The refusal of *named*, which chains nothing and quotes no stored secret."""
    with pytest.raises(NamedDocumentError) as caught:
        _ = config.named_validate(named, stored=stored)
    error = caught.value
    assert error.__cause__ is None
    assert error.__context__ is None
    text = "".join(traceback.format_exception(error))
    assert OLD not in text + repr(error) + str(error.args)
    return error


async def test_a_mask_in_a_configuration_read_without_stored_is_refused() -> None:
    config, named = await with_a_password()
    error = refusal_of(type(config), named, None)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=0)
    assert error.message == NO_PLACE.format(why="no `stored` was given")


async def test_a_mask_in_a_row_the_stored_configuration_lacks_is_refused() -> None:
    config, named = await with_a_password()
    stored = config.model_copy(
        update={"modules": [row for row in rows_of(config) if row.name == "QOS"]}
    )
    error = refusal_of(type(config), named, stored)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=0)
    assert error.message == NO_PLACE.format(
        why="`stored` has no module row named `deviceSettings`: the row is new"
    )
    without = config.model_copy(update={"modules": MISSING})
    assert refusal_of(type(config), named, without).message == error.message


async def test_a_row_the_stored_configuration_lacks_is_taken_with_its_secrets() -> None:
    config, named = await with_a_password()
    (row, *_) = named["modules"]
    neighbor_of(row)["md5_password"] = "given-in-full"
    for ref_rows in row["refs"].values():
        for ref_row in ref_rows if isinstance(ref_rows, list) else [ref_rows]:
            ref_row["data"] = {}
    stored = config.model_copy(update={"modules": MISSING})
    again = read_back(stored, named)
    assert password_sent_by(again) == "given-in-full"
    assert [r.name for r in rows_of(again)] == ["deviceSettings", "QOS", "firewall"]


async def test_a_mask_in_a_row_whose_name_the_document_has_twice_is_refused() -> None:
    config, named = await with_a_password()
    named["modules"].append(copy.deepcopy(named["modules"][0]))
    error = refusal_of(type(config), named, config)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=0)
    assert error.message == NO_PLACE.format(
        why=(
            "2 module rows of the document are named `deviceSettings`, so the name "
            "does not say which stored row this one is"
        )
    )


async def test_a_mask_in_a_row_whose_name_stored_has_twice_is_refused() -> None:
    config, named = await with_a_password()
    stored = config.model_copy(
        update={"modules": [*rows_of(config), rows_of(config)[0]]}
    )
    error = refusal_of(type(config), named, stored)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=0)
    assert error.message == NO_PLACE.format(
        why=(
            "2 module rows of `stored` are named `deviceSettings`, so the name does "
            "not say which stored row this one is"
        )
    )


async def test_rows_of_one_name_without_a_mask_are_read_as_given() -> None:
    config = (await rule_uses_hub()).profile_config
    named = named_of(config)
    (firewall,) = [row for row in named["modules"] if row["name"] == "firewall"]
    assert HIDDEN not in json.dumps(firewall)
    named["modules"].append(copy.deepcopy(firewall))
    document = document_of(config, named)
    assert json.dumps(document["modules"][-1]) == json.dumps(document["modules"][-2])
    assert "configurationId" in document["modules"][-1]


# --- A row that is no row, and a document that is not JSON --------------------


@pytest.mark.parametrize(
    "row",
    [
        "deviceSettings",
        7,
        None,
        ["deviceSettings"],
        {"data": {}},
        {"name": "noSuchModule", "data": {"md5_password": "***"}},
        {"name": 7},
    ],
)
async def test_a_row_no_class_reads_is_passed_on_and_validation_refuses_it(
    row: object,
) -> None:
    config, named = await with_a_password()
    named["modules"].append(row)
    document = document_of(config, named)
    assert document["modules"][-1] == row
    wire = wire_of(config)["modules"]
    assert json.dumps(document["modules"][:-1]) == json.dumps(wire)
    error = refusal_of(type(config), named, config)
    assert error.kind == "invalid_value"
    assert error.path == ""
    assert "modules[#3]" in error.message
    assert "EnterpriseConfigurationProfile" in error.message


@pytest.mark.parametrize("modules", ["rows", {"name": "QOS"}, None, 7])
async def test_modules_that_are_no_list_are_passed_on_and_validation_refuses_them(
    modules: object,
) -> None:
    config, named = await with_a_password()
    named["modules"] = modules
    assert document_of(config, named)["modules"] == modules
    error = refusal_of(type(config), named, config)
    assert error.kind == "invalid_value"
    assert "modules: " in error.message


async def test_a_value_validation_refuses_in_a_row_is_named_from_the_document() -> None:
    # The place below a row is as validation has it: after the row's position
    # its module name, then the VCO's keys.
    config, named = await with_a_password()
    neighbor_of(named["modules"][0])["neighbor_as"] = 65010
    error = refusal_of(type(config), named, config)
    assert error.kind == "invalid_value"
    assert error.path == ""
    assert (
        "modules[#0].deviceSettings.data.segments[#0].bgp.neighbors[#0].neighborAS: "
        "Input should be a valid string"
    ) in error.message


async def test_a_vco_key_in_a_row_is_refused_at_its_place_in_the_document() -> None:
    config, named = await with_a_password()
    row = named["modules"][1]
    row["configurationId"] = row.pop("configuration_id")
    error = refusal_of(type(config), named, config)
    assert error.kind == "vco_key"
    assert error.path == "modules[#1].configuration_id"
    assert "`configurationId` is the VCO's key for `configuration_id`" in error.message


async def test_a_vco_key_of_the_configuration_is_refused() -> None:
    config, named = await with_a_password()
    named["id"] = named.pop("id_")
    error = refusal_of(type(config), named, config)
    assert error.kind == "vco_key"
    assert error.path == "id_"


@pytest.mark.parametrize(
    "value",
    [uuid.UUID(int=7), {1, 2}, MISSING, b"bytes"],
    ids=lambda value: type(value).__name__,
)
async def test_what_json_does_not_have_is_refused_wherever_it_is_in_a_row(
    value: object,
) -> None:
    config, named = await with_a_password()
    neighbor_of(named["modules"][0])["neighbor_as"] = value
    place = "modules[#0].data.segments[#0].bgp.neighbors[#0].neighbor_as"
    for read in (document_of, read_back):
        with pytest.raises(TypeError) as caught:
            _ = read(config, named)
        text = str(caught.value)
        assert text.startswith(f"{place}: a named document is JSON")
        assert type(value).__name__ in text
        assert OLD not in text
    named["modules"].append({"name": "noSuchModule", "at": value})
    named["modules"][0] = "no row"
    with pytest.raises(TypeError, match=r"^modules\[#3\]\.at: a named document"):
        _ = document_of(config, named)


async def test_a_mapping_and_a_tuple_are_read_in_a_row_as_json_reads_them() -> None:
    config, named = await with_a_password()
    wire = wire_of(config)
    named["modules"] = tuple(named["modules"])
    document = document_of(config, named)
    assert json.dumps(document) == json.dumps(wire)
    assert NEIGHBOR_IP in json.dumps(document)


# --- What "Dumps" in CONFIGURATION.md says of a named document ----------------


async def test_a_named_dump_given_to_model_validate_keeps_its_names_and_masks() -> None:
    _, named = await with_a_password()
    (row, *_) = named["modules"]
    del row["refs"]
    taken = DeviceSettingsConfigurationModule.model_validate(row)
    raw = json.dumps(taken.raw_data)
    assert '"md5_password": "***"' in raw
    assert "md5Password" not in raw


async def test_a_profiles_model_is_read_back_at_its_level_with_the_context() -> None:
    data = (await state_with_a_bgp_password(OLD)).profile.device_settings
    named = named_of(data)
    level: dict[str, object] = {"config_level": ConfigContext.PROFILE}
    again = type(data).named_validate(named, stored=data, context=level)
    assert again.config_context is ConfigContext.PROFILE
    assert named_of(again) == named
    plain = type(data).named_validate(named, stored=data)
    assert plain.config_context is None
    assert set(named_of(plain)) - set(named) == {
        "routed_interfaces",
        "loopback_interfaces",
    }


async def test_a_field_stored_under_two_keys_is_dumped_under_both_and_refused() -> None:
    config, _ = await with_a_password()
    wire = wire_of(config)
    wire["modules"][0]["data"]["segments"][0]["routes"] = {
        "icmpProbes": [],
        "icmp_probes": [],
    }
    stored = type(config).model_validate(wire)
    named = named_of(stored)
    routes = named["modules"][0]["data"]["segments"][0]["routes"]
    assert {"icmpProbes", "icmp_probes"} <= set(routes)
    error = refusal_of(type(config), named, stored)
    assert error.kind == "vco_key"
    assert error.path == "modules[#0].data.segments[#0].routes.icmp_probes"


async def test_a_shown_segment_ref_row_does_not_show_its_segment_id() -> None:
    config, named = await with_a_password()
    shown = named["modules"][0]["refs"]["segment"]
    assert [row["data"] for row in shown] == [HIDDEN, HIDDEN]
    assert "segmentId" not in json.dumps(shown)
    assert "segment_id" not in json.dumps(shown)
    stored = wire_of(config)["modules"][0]["refs"]["deviceSettings:segment"]
    assert [row["data"]["segmentId"] for row in stored] == [0, 2]
