import copy
import json

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.models.configuration import FirewallData
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    ConfigurationModuleList,
    EdgeConfiguration,
    FirewallConfigurationModule,
    GenericConfigurationModule,
    QosConfigurationModule,
    new_edge_module,
)
from pyvelo.testing import make_module_json

FW_RAW = {
    "segments": [
        {
            "segment": {"segmentId": 0, "name": "Global Segment"},
            "outbound": [],
            "futureKey": 1,
        }
    ],
    "syslog_forwarding": False,
    "futureTopLevel": [1, 2.5, None],
}


def test_capture_from_json_through_the_discriminated_union():
    rows = [
        make_module_json("firewall", FW_RAW),
        make_module_json("QOS", {"segments": []}, module_id=2),
    ]
    modules = ConfigurationModuleList.model_validate_json(json.dumps(rows)).root
    assert isinstance(modules[0], FirewallConfigurationModule)
    assert modules[0].raw_data == FW_RAW
    assert modules[0].raw_mode == "json"
    assert modules[0].raw_refs is MISSING
    assert modules[0].is_new is False
    assert isinstance(modules[1], QosConfigurationModule)


def test_capture_from_python_dicts_is_a_copy():
    row = make_module_json("firewall", copy.deepcopy(FW_RAW))
    cfg = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "modules": [row]}
    )
    row["data"]["segments"].clear()
    assert cfg.firewall.raw_data == FW_RAW
    assert cfg.firewall.raw_mode == "python"


def test_keyword_construction_snapshots_its_own_dump():
    data = FirewallData(segments=[])
    mod = FirewallConfigurationModule.model_validate(
        {**make_module_json("firewall", {}), "data": data}
    )
    assert mod.raw_data == data.model_dump(mode="json", by_alias=True)
    data.firewall_enabled = False
    assert isinstance(mod.raw_data, dict)
    assert mod.raw_data["firewall_enabled"] is True


def test_existing_instance_keeps_its_snapshot():
    mod = FirewallConfigurationModule.model_validate(
        make_module_json("firewall", FW_RAW)
    )
    cfg = EdgeConfiguration(name="Edge Specific Profile", modules=[mod])
    assert cfg.modules is not MISSING
    assert cfg.modules[0] is mod
    assert copy.deepcopy(mod).raw_data == FW_RAW
    rebuilt = EdgeConfiguration.model_construct(**dict(cfg))
    assert rebuilt.firewall.raw_data == FW_RAW


def test_capture_tolerates_missing_and_untyped():
    row = make_module_json("firewall", {})
    del row["data"]
    bare = ConfigurationModuleBase.model_validate(row)
    assert bare.raw_data is MISSING and bare.raw_refs is MISSING
    qos = QosConfigurationModule.model_validate(
        make_module_json("QOS", {"segments": []}, refs={})
    )
    assert qos.raw_refs == {}
    generic = GenericConfigurationModule.model_validate(
        make_module_json("analyticsSettings", {"anything": [1]})
    )
    assert generic.raw_data == {"anything": [1]}


def test_new_edge_module():
    mod = new_edge_module(ConfigurationModuleName.FIREWALL, configuration_id=10)
    assert isinstance(mod, FirewallConfigurationModule)
    assert (mod.is_new, mod.raw_data, mod.configuration_id) == (
        True,
        {"segments": []},
        10,
    )
    assert mod.data.segments == []
    assert new_edge_module(ConfigurationModuleName.QOS, 10).raw_data == {"segments": []}
    with pytest.raises(ValueError, match="deviceSettings"):
        new_edge_module(ConfigurationModuleName.DEVICE_SETTINGS, 10)
