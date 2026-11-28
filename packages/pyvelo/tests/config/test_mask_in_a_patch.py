"""The mask of a secret, given in a patch, keeps the stored secret.

``named_dump`` and everything else pyvelo shows write ``"***"`` in place of a
secret, in the keys a patch has. So a caller that copies what was shown into
a patch gives the mask back. As the whole value of a field marked ``Secret``
it means there what it means in a named document: keep the stored value. The
patch plans no change for the field, and nothing is sent for it. Where
nothing is stored to keep (the field is unset, or the patch builds the object)
the patch is refused, since the mask would be written to the VCO as the secret.
"""

import json
from typing import Annotated, Any, Final

import pytest
from hub_state import (
    NEIGHBOR_IP,
    NEW_PASSWORD,
    NEW_PASSWORD_PATCH,
    OLD_PASSWORD,
    bgp_with_a_password,
    hub_pipeline,
    state_with_a_bgp_password,
)
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.device_settings.segment import BgpNeighbor
from pyvelo.config.state import ConfigState
from pyvelo.core import HIDDEN, KeyField, PatchError, Positional, Secret, VcoModel, add

NEIGHBORS: Final = 'segments["Global Segment"].bgp.neighbors'
NEIGHBOR: Final = f'{NEIGHBORS}["{NEIGHBOR_IP}"]'


def neighbors_patch(neighbors: object) -> dict[str, Any]:
    """A device settings patch that gives *neighbors* to the Global Segment's BGP."""
    return {"segments": {"Global Segment": {"bgp": {"neighbors": neighbors}}}}


def stored_password(state: ConfigState) -> object:
    """The password the state's model holds for the neighbour ``NEIGHBOR_IP``."""
    for segment in state.profile.device_settings.segments:
        if segment.bgp is not MISSING and segment.bgp.neighbors:
            (neighbor,) = segment.bgp.neighbors
            return neighbor.md5_password
    raise AssertionError("the state has no BGP neighbour")


def device_settings_writes(requests: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """The text of each request that writes the device settings."""
    return [
        json.dumps(params)
        for method, params in requests
        if "update" in method.lower() and "md5Password" in json.dumps(params)
    ]


async def test_a_mask_for_a_stored_secret_plans_nothing_and_sends_nothing() -> None:
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    patch = neighbors_patch({NEIGHBOR_IP: {"md5_password": HIDDEN}})
    _ = await pipeline.apply(state, module="device_settings", patch=patch)

    assert stored_password(state) == OLD_PASSWORD
    assert state.get_field_changes() == []
    assert all(module.operation == "none" for module in state.plan().modules)
    since = len(store.requests)
    _ = await pipeline.save(state)
    assert device_settings_writes(store.requests[since:]) == []


async def test_a_mask_beside_a_change_keeps_the_secret_in_what_is_sent() -> None:
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    patch = neighbors_patch(
        {NEIGHBOR_IP: {"md5_password": HIDDEN, "neighbor_as": "65009"}}
    )
    _ = await pipeline.apply(state, module="device_settings", patch=patch)

    assert [str(change.path) for change in state.get_field_changes()] == [
        f"{NEIGHBOR}.neighbor_as"
    ]
    assert "md5_password" not in state.plan().render()
    since = len(store.requests)
    _ = await pipeline.save(state)
    (sent,) = device_settings_writes(store.requests[since:])
    assert f'"md5Password": "{OLD_PASSWORD}"' in sent
    assert '"neighborAS": "65009"' in sent
    assert f'"{HIDDEN}"' not in sent


async def test_a_new_secret_given_in_full_is_still_written() -> None:
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    _ = await pipeline.apply(state, module="device_settings", patch=NEW_PASSWORD_PATCH)

    assert stored_password(state) == NEW_PASSWORD
    since = len(store.requests)
    _ = await pipeline.save(state)
    (sent,) = device_settings_writes(store.requests[since:])
    assert f'"md5Password": "{NEW_PASSWORD}"' in sent


def every_key_of_a_neighbour() -> dict[str, Any]:
    """A BGP neighbour as the VCO stores one, with every key it writes."""
    sparse = bgp_with_a_password(OLD_PASSWORD)["neighbors"][0]
    return BgpNeighbor.model_validate(sparse).model_dump(mode="json", by_alias=True)


async def test_a_neighbour_copied_whole_from_the_named_dump_plans_no_change() -> None:
    # The neighbour is stored with every key: a key the VCO did not store
    # reads as its default, and a patch that gives that default is refused as
    # an edit a save cannot write, mask or no mask.
    state = await state_with_a_bgp_password(OLD_PASSWORD, **every_key_of_a_neighbour())
    pipeline, store = hub_pipeline(state, vpn_check=True)
    named: dict[str, Any] = state.profile_config.device_settings.named_dump()
    (shown,) = [
        neighbor
        for segment in named["data"]["segments"]
        for neighbor in segment.get("bgp", {}).get("neighbors", [])
    ]
    assert shown["md5_password"] == HIDDEN
    assert shown["neighbor_ip"] == NEIGHBOR_IP

    _ = await pipeline.apply(
        state, module="device_settings", path=NEIGHBOR, patch=shown
    )

    assert stored_password(state) == OLD_PASSWORD
    assert state.get_field_changes() == []
    assert all(module.operation == "none" for module in state.plan().modules)
    since = len(store.requests)
    _ = await pipeline.save(state)
    assert device_settings_writes(store.requests[since:]) == []


def refused(caught: pytest.ExceptionInfo[PatchError], path: str) -> str:
    """The message of a refused mask at *path*, checked for what every one says."""
    error = caught.value
    assert (error.kind, str(error.path)) == ("masked_secret", path)
    assert "would be written to the VCO as the secret" in error.message
    assert "Give the secret in full" in error.message
    return error.message


async def test_a_mask_in_a_new_neighbour_is_refused() -> None:
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    pipeline, _ = hub_pipeline(state, vpn_check=True)
    body = {"neighbor_as": "65001", "enable_md5": True, "md5_password": HIDDEN}
    with pytest.raises(PatchError) as caught:
        _ = await pipeline.apply(
            state,
            module="device_settings",
            patch=neighbors_patch({"192.0.2.7": add(body)}),
        )
    message = refused(caught, f'{NEIGHBORS}["192.0.2.7"].md5_password')
    assert "the patch builds" in message
    assert "leave md5_password out" in message
    assert state.get_field_changes() == []
    assert stored_password(state) == OLD_PASSWORD


async def test_a_mask_in_an_item_of_a_list_value_is_refused() -> None:
    # A list value replaces the list: each item of it is built, and holds
    # nothing of the stored item it stands for.
    state = await state_with_a_bgp_password(OLD_PASSWORD)
    pipeline, _ = hub_pipeline(state, vpn_check=True)
    item = {"neighbor_ip": NEIGHBOR_IP, "neighbor_as": "65001", "md5_password": HIDDEN}
    with pytest.raises(PatchError) as caught:
        _ = await pipeline.apply(
            state, module="device_settings", patch=neighbors_patch([item])
        )
    message = refused(caught, f"{NEIGHBOR}.md5_password")
    assert "change that item under its selector" in message
    assert stored_password(state) == OLD_PASSWORD


# --- the rule on models of its own --------------------------------------------


class Login(VcoModel):
    user_name: str = ""
    pass_word: Annotated[str | None | MISSING, Secret()] = MISSING


class Token(VcoModel):
    """A model that cannot be built from its defaults."""

    name: str
    value: Annotated[str, Secret()]


class Vault(VcoModel):
    login: Login | MISSING = MISSING
    token: Token | MISSING = MISSING
    logins: Annotated[list[Login], KeyField("user_name")] = Field(default_factory=list)
    by_site: dict[str, Login] = Field(default_factory=dict)
    words: Annotated[list[str], Positional(), Secret()] = Field(default_factory=list)


def vault() -> Vault:
    return Vault.model_validate(
        {
            "login": {"userName": "a", "passWord": "s-a"},
            "logins": [
                {"userName": "b", "passWord": "s-b"},
                {"userName": "c"},
                {"userName": "d", "passWord": None},
            ],
            "bySite": {"x": {"userName": "f", "passWord": "s-f"}},
            "words": ["w1", "w2"],
        }
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"login": {"pass_word": HIDDEN}},
        {"login": {"passWord": HIDDEN}},
        {"logins": {"b": {"pass_word": HIDDEN}}},
        {"by_site": {"x": {"pass_word": HIDDEN}}},
        # A stored null is a stored value: a named dump masks it too.
        {"logins": {"d": {"pass_word": HIDDEN}}},
        # A secret that is a list is masked whole.
        {"words": HIDDEN},
    ],
)
def test_a_mask_keeps_what_the_model_holds(patch: dict[str, Any]) -> None:
    model = vault()
    before = model.model_dump(mode="json")
    fields_set = set(model.model_fields_set)
    assert model.merge_patch(patch) is model
    assert model.model_dump(mode="json") == before
    assert model.model_fields_set == fields_set


def test_the_rest_of_a_patch_with_a_mask_is_merged() -> None:
    model = vault()
    _ = model.merge_patch({"login": {"user_name": "z", "pass_word": HIDDEN}})
    assert model.login is not MISSING
    assert (model.login.user_name, model.login.pass_word) == ("z", "s-a")


@pytest.mark.parametrize(
    ("patch", "path", "says"),
    [
        # The field is unset on the stored item.
        ({"logins": {"c": {"pass_word": HIDDEN}}}, "logins[c].pass_word", "holds no"),
        # An add, in a keyed list and in a map.
        (
            {"logins": {"n": add({"pass_word": HIDDEN})}},
            "logins[n].pass_word",
            "the patch builds",
        ),
        (
            {"by_site": {"y": add({"pass_word": HIDDEN})}},
            "by_site[y].pass_word",
            "the patch builds",
        ),
        # A list value: every item of it is built.
        (
            {"logins": [{"user_name": "b", "pass_word": HIDDEN}]},
            "logins[b].pass_word",
            "the patch builds",
        ),
        # A sub-model that was unset, built from its fields.
        (
            {"token": {"name": "t", "value": HIDDEN}},
            "token.value",
            "the patch builds",
        ),
    ],
)
def test_a_mask_with_nothing_stored_to_keep_is_refused(
    patch: dict[str, Any], path: str, says: str
) -> None:
    model = vault()
    before = model.model_dump(mode="json")
    with pytest.raises(PatchError) as caught:
        _ = model.merge_patch(patch)
    assert says in refused(caught, path)
    assert "s-" not in str(caught.value)
    assert model.model_dump(mode="json") == before


def test_a_mask_in_a_sub_model_built_from_its_defaults_is_refused() -> None:
    model = Vault()
    with pytest.raises(PatchError) as caught:
        _ = model.merge_patch({"login": {"user_name": "a", "pass_word": HIDDEN}})
    assert "the patch builds" in refused(caught, "login.pass_word")
    assert model.login is MISSING


def test_the_mask_text_where_no_secret_is_declared_is_a_value() -> None:
    model = vault()
    _ = model.merge_patch({"login": {"user_name": HIDDEN}, "words": [HIDDEN]})
    assert model.login is not MISSING
    assert (model.login.user_name, model.login.pass_word) == (HIDDEN, "s-a")
    # An item of a secret that is a list is no mask: the list is given in full.
    assert model.words == [HIDDEN]
