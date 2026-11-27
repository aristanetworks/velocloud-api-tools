"""A configuration read back from its named dump sends what its wire dump would.

A configuration's ``named_dump`` holds each row of ``modules`` as that row's
own named dump: attribute names, every secret masked. ``named_validate`` reads
the document back row by row, each by the class its ``name`` has, with the
row of that name in ``stored`` giving the masks their values (among several
rows of one name, the row of its ID). A module row
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
    assert (change.before, change.after) == ("***", "***")
    _ = await pipeline.save(state)
    sent = json.dumps(store.requests)
    assert NEW in sent
    assert OLD not in sent


async def test_a_field_change_shows_no_secret() -> None:
    state = await state_with_a_bgp_password(OLD)
    _ = await ConfigPipeline(client=None).apply(
        state, module="device_settings", patch=NEW_PASSWORD_PATCH
    )
    changes = state.get_field_changes()
    assert changes
    assert not any(secret in repr(changes) for secret in (OLD, NEW))
    assert ("***", "***") in [(change.before, change.after) for change in changes]


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
NO_ROW: Final = (
    'holds the mask "***", and `stored` has no module row to keep its value from '
    "({why}). The mask would be written to the VCO as the secret. A row keeps the "
    "secrets of the stored row of its `name`, and where several rows have that "
    "name, of the one with its `id_`. Leave the `name` and the `id_` of a row as "
    "they were dumped, or write every secret of the row in full (in a device "
    "settings row, the `data` of each ref row is one)."
)
"""What refuses a mask in a row that has no stored row, where `stored` was given."""
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
    assert error.message == NO_ROW.format(
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
    assert error.message == NO_ROW.format(
        why=(
            "2 `deviceSettings` rows of the document have the `id_` 10, so the "
            "`id_` does not tell them apart"
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
    assert error.message == NO_ROW.format(
        why=(
            "2 `deviceSettings` rows of `stored` have the `id_` 10, so the `id_` "
            "does not tell them apart"
        )
    )


async def test_another_refusal_in_a_row_with_no_stored_row_keeps_its_own_text() -> None:
    config, named = await with_a_password()
    row = named["modules"][0]
    row["configurationId"] = row.pop("configuration_id")
    stored = config.model_copy(update={"modules": MISSING})
    error = refusal_of(type(config), named, stored)
    assert error.kind == "vco_key"
    assert error.path == "modules[#0].configuration_id"
    assert "`configurationId` is the VCO's key for `configuration_id`" in error.message


async def test_rows_of_one_name_without_a_mask_are_read_as_given() -> None:
    config = (await rule_uses_hub()).profile_config
    named = named_of(config)
    (firewall,) = [row for row in named["modules"] if row["name"] == "firewall"]
    assert HIDDEN not in json.dumps(firewall)
    named["modules"].append(copy.deepcopy(firewall))
    document = document_of(config, named)
    assert json.dumps(document["modules"][-1]) == json.dumps(document["modules"][-2])
    assert "configurationId" in document["modules"][-1]


# --- Rows of one name: its ID says which stored row a row is -----------------

OTHER: Final = "OTHER-md5-made-up"
"""The password in the second device settings row of ``with_two_rows``."""
OTHER_ID: Final = 77
"""The ID of that row."""
FIRST_ID: Final = 10
"""The ID of the device settings row the fixture profile has."""


async def with_two_rows(
    *, other_first: bool = False
) -> tuple[EnterpriseConfigurationProfile, dict[str, Any]]:
    """A profile with two device settings rows, and its named dump.

    What a repeated insert leaves: the second row is the first one with the ID
    ``OTHER_ID`` and the password ``OTHER``. It is the last row, or the first.
    """
    config, _ = await with_a_password()
    wire = wire_of(config)
    other = copy.deepcopy(wire["modules"][0])
    assert other["id"] == FIRST_ID
    other["id"] = OTHER_ID
    other["data"]["segments"][0]["bgp"]["neighbors"][0]["md5Password"] = OTHER
    wire["modules"].insert(0 if other_first else len(wire["modules"]), other)
    twice = type(config).model_validate(wire)
    return twice, named_of(twice)


def passwords_sent_by(config: Config) -> list[tuple[int, object]]:
    """Each device settings row's ID, with the password a save of it starts from."""
    sent: list[tuple[int, object]] = []
    for row in rows_of(config):
        if isinstance(row, DeviceSettingsConfigurationModule):
            raw: Any = row.raw_data
            (neighbor,) = raw["segments"][0]["bgp"]["neighbors"]
            sent.append((row.id_, neighbor["md5Password"]))
    return sent


def device_settings_of(named: dict[str, Any]) -> list[dict[str, Any]]:
    """The device settings rows of a named configuration, in its order."""
    return [row for row in named["modules"] if row["name"] == "deviceSettings"]


def in_full(row: dict[str, Any], password: str) -> None:
    """Write every secret of the named device settings *row* in full."""
    neighbor_of(row)["md5_password"] = password
    for ref_rows in row["refs"].values():
        for ref_row in ref_rows if isinstance(ref_rows, list) else [ref_rows]:
            ref_row["data"] = {}


@pytest.mark.parametrize("other_first", [False, True])
async def test_two_rows_of_one_name_are_read_back_each_with_its_own_secret(
    *, other_first: bool
) -> None:
    config, named = await with_two_rows(other_first=other_first)
    expected = [(FIRST_ID, OLD), (OTHER_ID, OTHER)]
    assert passwords_sent_by(config) == expected[:: -1 if other_first else 1]
    assert OLD not in json.dumps(named)
    assert OTHER not in json.dumps(named)
    assert [neighbor_of(row)["md5_password"] for row in device_settings_of(named)] == [
        HIDDEN,
        HIDDEN,
    ]

    assert json.dumps(document_of(config, named)) == json.dumps(wire_of(config))
    by_keys = type(config).model_validate(wire_of(config))
    by_name = read_back(config, named)
    assert json.dumps(wire_of(by_name)) == json.dumps(wire_of(by_keys))
    for kept, again in zip(rows_of(by_keys), rows_of(by_name), strict=True):
        assert json.dumps(again.raw_data) == json.dumps(kept.raw_data)
        assert again.raw_refs == kept.raw_refs
    assert passwords_sent_by(by_name) == passwords_sent_by(config)


async def test_rows_of_one_name_keep_their_secrets_wherever_they_are() -> None:
    config, named = await with_two_rows()
    named["modules"].reverse()
    again = read_back(config, named)
    assert passwords_sent_by(again) == [(OTHER_ID, OTHER), (FIRST_ID, OLD)]
    # One of the two alone is found by its ID too: `stored` has two of the name.
    del named["modules"][-1]
    assert passwords_sent_by(read_back(config, named)) == [(OTHER_ID, OTHER)]


async def test_a_row_given_the_id_of_another_of_its_name_takes_that_rows_secrets() -> (
    None
):
    """Among rows of one name the ID is the row's identity.

    A named dump shows it and nothing else tells the rows apart, so a row
    whose ``id_`` is set to that of another stored row is that row: its masks
    keep that row's secrets. The two IDs swapped swap the secrets.
    """
    config, named = await with_two_rows()
    first, other = device_settings_of(named)
    first["id_"], other["id_"] = other["id_"], first["id_"]
    again = read_back(config, named)
    assert passwords_sent_by(again) == [(OTHER_ID, OTHER), (FIRST_ID, OLD)]


async def test_a_mask_in_a_row_whose_id_no_stored_row_of_its_name_has_is_refused() -> (
    None
):
    config, named = await with_two_rows()
    device_settings_of(named)[1]["id_"] = 78
    error = refusal_of(type(config), named, config)
    assert OTHER not in "".join(traceback.format_exception(error)) + repr(error)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=3)
    assert error.message == NO_ROW.format(
        why="no `deviceSettings` row of `stored` has the `id_` 78: the row is new"
    )
    assert "Give the model" not in str(error)


async def test_a_row_whose_id_no_stored_row_has_is_taken_with_its_secrets() -> None:
    config, named = await with_two_rows()
    new = device_settings_of(named)[1]
    new["id_"] = 78
    in_full(new, "given-in-full")
    again = read_back(config, named)
    assert passwords_sent_by(again) == [(FIRST_ID, OLD), (78, "given-in-full")]


@pytest.mark.parametrize("given", [None, True, "77", 77.0, [77]])
async def test_a_mask_in_a_row_of_several_that_gives_no_id_is_refused(
    given: object,
) -> None:
    config, named = await with_two_rows()
    row = device_settings_of(named)[1]
    if given is None:
        del row["id_"]
    else:
        row["id_"] = given
    error = refusal_of(type(config), named, config)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=3)
    assert error.message == NO_ROW.format(
        why=(
            "several module rows are named `deviceSettings`, and this one gives "
            "no `id_`, a whole number, to say which stored row it is"
        )
    )


async def test_a_row_added_under_a_name_stored_has_once_is_a_new_row() -> None:
    config, named = await with_a_password()
    new = copy.deepcopy(named["modules"][0])
    new["id_"] = OTHER_ID
    named["modules"].append(new)
    error = refusal_of(type(config), named, config)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=3)
    assert error.message == NO_ROW.format(
        why="no `deviceSettings` row of `stored` has the `id_` 77: the row is new"
    )
    in_full(new, "given-in-full")
    again = read_back(config, named)
    assert passwords_sent_by(again) == [(FIRST_ID, OLD), (OTHER_ID, "given-in-full")]


async def test_an_id_two_rows_of_one_name_have_pairs_neither() -> None:
    config, named = await with_two_rows()
    first, other = device_settings_of(named)
    other["id_"] = first["id_"]
    error = refusal_of(type(config), named, config)
    assert error.kind == "masked_secret"
    assert error.path == PASSWORD.format(row=0)
    # Whichever of the two holds the mask is refused: neither is before the other.
    in_full(first, "given-in-full")
    assert refusal_of(type(config), named, config).path == PASSWORD.format(row=3)


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
    config, named = await with_a_password()
    neighbor_of(named["modules"][0])["neighbor_as"] = 65010
    error = refusal_of(type(config), named, config)
    assert error.kind == "invalid_value"
    assert error.path == ""
    assert (
        "modules[#0].data.segments[#0].bgp.neighbors[#0].neighbor_as: "
        "Input should be a valid string"
    ) in error.message
    assert "neighborAS" not in str(error)
    assert "deviceSettings" not in str(error)


async def test_each_value_validation_refuses_is_at_its_place_in_the_document() -> None:
    config, named = await with_a_password()
    named["id_"] = "no number"
    named["modules"][0]["id_"] = "no number"
    named["modules"][1]["data"]["segments"] = "no list"
    named["modules"][2]["type_"] = 7
    error = refusal_of(type(config), named, config)
    assert error.kind == "invalid_value"
    start = "this document is not a valid EnterpriseConfigurationProfile: "
    assert error.message.startswith(start)
    lines = error.message.removeprefix(start).split(". No model is made of it")[0]
    assert [line.split(": ")[0] for line in lines.split("; ")] == [
        "id_",
        "modules[#0].id_",
        "modules[#1].data.segments",
        "modules[#2].type_",
    ]


def test_a_place_below_a_row_is_named_by_the_rows_class() -> None:
    place = EnterpriseConfigurationProfile.named_place
    neighbor = ("segments", 0, "bgp", "neighbors", 0, "neighborAS")
    assert place(("modules", 0, "deviceSettings", "data", *neighbor)) == (
        "modules[#0].data.segments[#0].bgp.neighbors[#0].neighbor_as"
    )
    assert place(("modules", 4, "QOS", "configurationId")) == (
        "modules[#4].configuration_id"
    )
    assert place(("modules", 1, "firewall")) == "modules[#1]"
    assert place(("schemaVersion",)) == "schema_version"
    # The row read alone names the same place, from the row down.
    alone = DeviceSettingsConfigurationModule.named_place(("data", *neighbor))
    assert alone == "data.segments[#0].bgp.neighbors[#0].neighbor_as"


def test_a_place_in_a_row_no_class_reads_is_named_as_far_as_the_document_goes() -> None:
    place = EdgeConfiguration.named_place
    assert place(("modules", 3)) == "modules[#3]"
    assert place(("modules",)) == "modules"
    assert place(("modules", 0, "noSuchModule", "configurationId")) == (
        "modules[#0].noSuchModule.configurationId"
    )


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
