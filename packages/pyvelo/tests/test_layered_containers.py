"""Configuration containers, module rows and the composed read are typed per layer; the firewall models are layered."""

import copy
import json
import pickle
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import LayerUnknownError
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleList,
    ConfigurationModuleUpdate,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    GetConfigurationModules,
    InsertFirewallConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
    new_edge_module,
)
from pyvelo.client.schemas.edge import (
    CompositeFirewallModule,
    CompositeQosModule,
    EdgeConfigurationModulesResult,
    GetEdgeConfigurationModules,
)
from pyvelo.core import ConfigContext, add
from pyvelo.core.layers import Composed, Edge, Profile
from pyvelo.testing import ModuleStore, make_module_json, store_of

from ._type_support import MockVcoClientFactory, require_present

EDGE = ConfigContext.EDGE
PROFILE = ConfigContext.PROFILE
UPDATE = "configuration/updateConfigurationModule"


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


EDGE_RAW: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "outbound": [], "futureKey": 1}
    ],
    "syslog_forwarding": False,
    "firewall_logging_enabled": False,
    "futureTopLevel": [1, 2.5, None],
}
PROFILE_RAW: dict[str, Any] = {
    "firewall_enabled": True,
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outbound": [
                {"name": "allow-all", "match": {}, "action": {"allow_or_deny": "allow"}}
            ],
        },
        {"segment": desc(2, "Guest"), "outbound": []},
    ],
}


def row(raw: dict[str, Any], *, module_id: int = 1, configuration_id: int = 10):
    return make_module_json(
        "firewall",
        copy.deepcopy(raw),
        module_id=module_id,
        configuration_id=configuration_id,
        version="v1",
    )


# --- a stack parses each layer into its class -----------------------------


def test_a_stack_parses_each_layer_into_its_class():
    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "modules": [row(EDGE_RAW)]}
    )
    assert type(edge.firewall) is FirewallConfigurationModule[Edge]
    assert type(edge.firewall.data) is FirewallData[Edge]
    assert all(type(s) is FirewallSegment[Edge] for s in edge.firewall.data.segments)
    # The layer is in the class: nothing called for_edge() on any of them.
    assert edge.firewall.config_context is EDGE
    assert edge.firewall.data.config_context is EDGE
    assert edge.firewall.data.segments[0].config_context is EDGE

    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "Default Profile", "modules": [row(PROFILE_RAW, module_id=2)]}
    )
    assert type(profile.firewall) is FirewallConfigurationModule[Profile]
    assert type(profile.firewall.data) is FirewallData[Profile]
    assert all(
        type(s) is FirewallSegment[Profile] for s in profile.firewall.data.segments
    )
    assert profile.firewall.config_context is PROFILE
    assert profile.firewall.data.config_context is PROFILE
    assert profile.firewall.data.segments[1].config_context is PROFILE


def test_a_module_list_is_of_the_layer_it_is_parametrized_with():
    rows = [row(EDGE_RAW)]
    text = json.dumps(rows)
    edge = ConfigurationModuleList[Edge]
    assert type(edge.model_validate(rows).root[0]) is FirewallConfigurationModule[Edge]
    assert (
        type(edge.model_validate_json(text).root[0])
        is FirewallConfigurationModule[Edge]
    )
    profile = ConfigurationModuleList[Profile]
    assert (
        type(profile.model_validate(rows).root[0])
        is FirewallConfigurationModule[Profile]
    )
    assert (
        type(profile.model_validate_json(text).root[0])
        is FirewallConfigurationModule[Profile]
    )


def test_new_edge_module_is_an_edge_firewall_module():
    module = new_edge_module(ConfigurationModuleName.FIREWALL, 10)
    assert type(module) is FirewallConfigurationModule[Edge]
    assert type(module.data) is FirewallData[Edge]
    assert module.is_new
    assert module.config_context is EDGE


def test_an_unparametrized_firewall_module_does_not_validate():
    with pytest.raises(TypeError, match=r"FirewallData\[Edge\] or FirewallData\[Prof"):
        _ = FirewallConfigurationModule.model_validate(row(EDGE_RAW))
    with pytest.raises(TypeError, match="parametrize"):
        _ = ConfigurationModuleList.model_validate([row(EDGE_RAW)])
    with pytest.raises(TypeError, match="parametrize"):
        _ = FirewallData.model_validate({"segments": []})


def test_the_classes_of_a_layer_pickle():
    module = FirewallConfigurationModule[Profile].model_validate(
        row(PROFILE_RAW, module_id=2)
    )
    copied = pickle.loads(pickle.dumps(module))
    assert type(copied) is FirewallConfigurationModule[Profile]
    assert type(copied.data) is FirewallData[Profile]
    assert copied.raw_data == PROFILE_RAW
    assert pickle.loads(pickle.dumps(FirewallSegment[Edge])) is FirewallSegment[Edge]


# --- get_configuration learns the layer ---------------------------------


@pytest.mark.parametrize(
    ("name", "config_class", "layer"),
    [
        ("Edge Specific Profile", EdgeConfiguration, Edge),
        ("Default Profile", EnterpriseConfigurationProfile, Profile),
    ],
)
async def test_get_configuration_validates_into_the_layer_it_learns(
    mock_vco_client: MockVcoClientFactory,
    name: str,
    config_class: type,
    layer: type,
):
    store = ModuleStore()
    store.add_configuration(
        {"id": 10, "name": name, "modules": [row(EDGE_RAW, configuration_id=10)]}
    )
    client = await mock_vco_client(store)

    config = await client.get_configuration(
        GetConfiguration(id_=10, with_=[GetConfigurationWith.MODULES])
    )

    assert type(config) is config_class
    assert type(config.firewall) is FirewallConfigurationModule[layer]
    assert type(config.firewall.data) is FirewallData[layer]
    assert config.firewall.raw_data == EDGE_RAW
    assert config.firewall.raw_mode == "json"
    assert config.config_context is (EDGE if layer is Edge else PROFILE)


async def test_get_configuration_modules_needs_the_layer(
    mock_vco_client: MockVcoClientFactory,
):
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Default Profile"})
    store.rows[1] = row(PROFILE_RAW)
    client = await mock_vco_client(store)
    request = GetConfigurationModules(configuration_id=10)

    with pytest.raises(TypeError, match="layer"):
        _ = await client.get_configuration_modules(request)  # pyright: ignore[reportCallIssue]

    (listed,) = await client.get_configuration_modules(request, layer=Profile)
    assert type(listed) is FirewallConfigurationModule[Profile]
    assert listed.refs_loaded is False
    (listed,) = await client.get_configuration_modules(request, layer=Edge)
    assert type(listed) is FirewallConfigurationModule[Edge]


async def test_a_firewall_insert_that_asks_for_its_row_names_the_layer_before_it_is_sent(
    mock_vco_client: MockVcoClientFactory,
):
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Default Profile"})
    client = await mock_vco_client(store)
    insert = InsertFirewallConfigurationModule(
        configuration_id=10, data={"segments": []}, return_data=True
    )

    with pytest.raises(TypeError, match=r"level=ConfigContext\.EDGE.*Nothing was sent"):
        _ = await client.insert_configuration_module(insert)
    assert store.requests == []

    inserted = await client.insert_configuration_module(insert, level=PROFILE)
    assert type(inserted) is FirewallConfigurationModule[Profile]


async def test_an_update_answered_with_a_firewall_row_names_the_layer(
    mock_vco_client: MockVcoClientFactory,
):
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Default Profile"})
    store.rows[1] = row(PROFILE_RAW)
    client = await mock_vco_client(store)

    def update() -> UpdateConfigurationModule:
        return UpdateConfigurationModule(
            configuration_module_id=1,
            version="v1",
            update=ConfigurationModuleUpdate(data=copy.deepcopy(PROFILE_RAW)),
            return_data=True,
        )

    # The request does not name its module, so this is known only from the answer.
    with pytest.raises(
        LayerUnknownError, match=r"was sent.*level=ConfigContext\.PROFILE"
    ) as caught:
        _ = await client.update_configuration_module(update())
    assert len(store.requests) == 1
    assert caught.value.module_id == 1
    assert caught.value.answer == store.rows[1]  # as the VCO sent it
    updated = await client.update_configuration_module(update(), level=PROFILE)
    assert type(updated) is FirewallConfigurationModule[Profile]


async def test_a_write_that_does_not_ask_for_its_row_needs_no_layer(
    mock_vco_client: MockVcoClientFactory,
):
    store = ModuleStore()
    store.add_configuration({"id": 10, "name": "Default Profile"})
    client = await mock_vco_client(store)
    result = await client.insert_configuration_module(
        InsertFirewallConfigurationModule(configuration_id=10, data={"segments": []})
    )
    assert not isinstance(result, FirewallConfigurationModule)


# --- the composed read -------------------------------------------------------

COMPOSED: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "outbound": []}],
    "firewall_enabled": True,
    "futureKey": {"kept": [1, 2]},
}


def composed_response() -> dict[str, Any]:
    return {
        "firewall": {
            "version": "1718888888000",
            "schemaVersion": "3.0.0",
            "type": "ENTERPRISE",
            "data": copy.deepcopy(COMPOSED),
        }
    }


def test_the_composed_read_is_typed_composed():
    for result in (
        EdgeConfigurationModulesResult.model_validate(composed_response()),
        EdgeConfigurationModulesResult.model_validate_json(
            json.dumps(composed_response())
        ),
    ):
        firewall = require_present(result.firewall)
        assert isinstance(firewall, CompositeFirewallModule)
        assert type(firewall.data) is FirewallData[Composed]
        assert type(firewall.data.segments[0]) is FirewallSegment[Composed]
        assert firewall.type_ == ConfigurationModuleType.ENTERPRISE
        # What the models drop is still there to compare with.
        assert firewall.raw_data == COMPOSED
        assert not hasattr(firewall.data, "future_key")


def test_a_composed_module_cannot_be_edited():
    result = EdgeConfigurationModulesResult.model_validate(composed_response())
    firewall = require_present(result.firewall)
    assert isinstance(firewall, CompositeFirewallModule)
    with pytest.raises(TypeError, match="Composed"):
        firewall.data.firewall_enabled = False


def test_the_raw_data_of_a_composed_module_shares_nothing_with_the_input():
    response = composed_response()
    result = EdgeConfigurationModulesResult.model_validate(response)
    response["firewall"]["data"]["futureKey"]["kept"].clear()
    firewall = require_present(result.firewall)
    assert isinstance(firewall, CompositeFirewallModule)
    assert firewall.raw_data == COMPOSED


def test_the_composed_read_asks_for_encrypted_keys_only_when_told_to():
    plain = GetEdgeConfigurationModules(edge_id=1)
    assert plain.encrypt_keys is MISSING
    assert "encryptKeys" not in plain.model_dump(by_alias=True, mode="json")
    asked = GetEdgeConfigurationModules(edge_id=1, encrypt_keys=True)
    assert asked.model_dump(by_alias=True, mode="json")["encryptKeys"] is True


# --- a firewall save is unchanged ------------------------------------------


def disable(module: Any) -> None:
    module.data.firewall_enabled.set_local(False)


def block_a_rule(module: Any) -> None:
    _ = module.data.merge_patch(
        {
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block": add({"action": {"allow_or_deny": "deny"}, "match": {}})
                    }
                }
            }
        }
    )


@pytest.mark.parametrize(
    ("layer", "raw", "edit", "paths", "sent"),
    [
        pytest.param(
            Edge,
            EDGE_RAW,
            disable,
            ["firewall_enabled"],
            {**EDGE_RAW, "firewall_enabled": False},
            id="edge",
        ),
        pytest.param(
            Profile,
            PROFILE_RAW,
            block_a_rule,
            ['segments["Global Segment"].outbound[block]'],
            None,
            id="profile",
        ),
    ],
)
async def test_a_firewall_save_is_unchanged(
    mock_vco_client: MockVcoClientFactory,
    layer: Any,
    raw: dict[str, Any],
    edit: Any,
    paths: list[str],
    sent: dict[str, Any] | None,
):
    level = EDGE if layer is Edge else PROFILE
    module = FirewallConfigurationModule[layer].model_validate(row(raw))
    edit(module)
    plan = plan_module(module, level=level)
    assert plan.operation == "update"
    assert [str(c.path) for c in plan.changes] == paths
    if sent is not None:
        assert plan.data == sent
        assert [(c.before, c.after) for c in plan.changes] == [(MISSING, False)]
    else:
        assert "outboundOverride" not in plan.data["segments"][0]
        assert plan.data["segments"][0]["outbound"][-1]["name"] == "block"

    store = store_of(module)
    client = await mock_vco_client(store)
    stored = await client.update_module(module, module.data)
    ((method, params),) = store.requests
    assert method == UPDATE
    assert params["_update"]["data"] == plan.data
    assert params["version"] == "v1"
    assert type(stored) is FirewallConfigurationModule[layer]
    assert stored.config_context is level
    assert stored.raw_data == plan.data


# --- the QoS models are layered -----------------------------------------------

QOS_RAW: dict[str, Any] = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "rules": [],
            "futureKey": 1,
        }
    ],
    "futureTopLevel": 1,
}


def qos_row(*, module_id: int = 3, configuration_id: int = 10) -> dict[str, Any]:
    return make_module_json(
        "QOS",
        copy.deepcopy(QOS_RAW),
        module_id=module_id,
        configuration_id=configuration_id,
    )


def test_a_qos_row_is_typed_by_its_layer():
    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "modules": [qos_row()]}
    )
    assert type(edge.qos) is QosConfigurationModule[Edge]
    assert type(edge.qos.data) is QosData[Edge]
    assert all(type(s) is QosSegment[Edge] for s in edge.qos.data.segments)
    assert edge.qos.config_context is EDGE
    assert edge.qos.data.segments[0].config_context is EDGE

    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "Default Profile", "modules": [qos_row(module_id=4)]}
    )
    assert type(profile.qos) is QosConfigurationModule[Profile]
    assert type(profile.qos.data) is QosData[Profile]
    assert all(type(s) is QosSegment[Profile] for s in profile.qos.data.segments)
    assert profile.qos.config_context is PROFILE
    assert profile.qos.data.segments[0].config_context is PROFILE

    result = EdgeConfigurationModulesResult.model_validate(
        {
            "QOS": {
                "version": "1718888888000",
                "schemaVersion": "3.0.0",
                "type": "ENTERPRISE",
                "data": copy.deepcopy(QOS_RAW),
            }
        }
    )
    composed = require_present(result.qos)
    assert isinstance(composed, CompositeQosModule)
    assert type(composed.data) is QosData[Composed]
    assert type(composed.data.segments[0]) is QosSegment[Composed]
    assert composed.raw_data == QOS_RAW

    with pytest.raises(TypeError, match=r"QosData\[Edge\] or QosData\[Prof"):
        _ = QosData.model_validate({})
    with pytest.raises(TypeError, match=r"QosData\[Edge\]"):
        _ = QosConfigurationModule.model_validate(qos_row())


def test_new_edge_module_is_an_edge_qos_module():
    module = new_edge_module(ConfigurationModuleName.QOS, 10)
    assert type(module) is QosConfigurationModule[Edge]
    assert type(module.data) is QosData[Edge]
    assert module.is_new
    assert module.config_context is EDGE


def test_a_qos_segment_made_by_the_data_is_of_its_layer():
    guest = {"segments": [{"segment": desc(2, "Guest")}]}
    edge = QosData[Edge].model_validate({"segments": []})
    edge.bind_profile(guest)
    made_for_the_edge = edge.segment("Guest")
    assert type(made_for_the_edge) is QosSegment[Edge]
    assert edge.get_segment(name="Guest") is made_for_the_edge

    profile = QosData[Profile].model_validate({"segments": []})
    profile.bind_profile(guest)
    made_for_the_profile = profile.segment("Guest")
    assert type(made_for_the_profile) is QosSegment[Profile]
    assert profile.get_segment(name="Guest") is made_for_the_profile


def test_a_qos_module_planned_at_each_layer_writes_the_raw_data():
    edge = QosConfigurationModule[Edge].model_validate(qos_row())
    assert plan_module(edge, level=EDGE).operation == "none"
    profile = QosConfigurationModule[Profile].model_validate(qos_row())
    assert plan_module(profile, level=PROFILE).operation == "none"
