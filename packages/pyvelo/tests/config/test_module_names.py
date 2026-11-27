"""Each configuration module's python name, and the names input accepts."""

import pytest
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    python_module_name,
)
from pyvelo.config.module_names import resolve_module_name

PYTHON_NAMES = {
    "metaData": "meta_data",
    "properties": "properties",
    "analyticsSettings": "analytics_settings",
    "imageUpdate": "image_update",
    "controlPlane": "control_plane",
    "managementPlane": "management_plane",
    "deviceSettings": "device_settings",
    "WAN": "wan",
    "QOS": "business_policy",
    "firewall": "firewall",
    "atpMetadata": "atp_metadata",
}


def test_every_module_has_a_python_name() -> None:
    assert {m.value: python_module_name(m) for m in ConfigurationModuleName} == (
        PYTHON_NAMES
    )


def test_either_name_resolves() -> None:
    for wire, name in PYTHON_NAMES.items():
        assert resolve_module_name(name) is ConfigurationModuleName(wire)
        assert resolve_module_name(wire) is ConfigurationModuleName(wire)


def test_a_text_that_is_no_vco_name_is_kept() -> None:
    assert python_module_name("business_policy") == "business_policy"
    assert python_module_name("x") == "x"


def test_qos_is_not_a_module_name() -> None:
    with pytest.raises(ValueError, match="Unknown module") as error:
        _ = resolve_module_name("qos")
    assert str(error.value) == (
        "Unknown module: 'qos'. Give a module by its name: analytics_settings, "
        "atp_metadata, business_policy, control_plane, device_settings, firewall, "
        "image_update, management_plane, meta_data, properties, wan (the VCO's "
        "own name, such as QOS or deviceSettings, is accepted too)"
    )


def test_the_python_name_is_importable_beside_the_enum() -> None:
    from pyvelo.config import (  # noqa: PLC0415 (the import is what this test checks)
        python_module_name as exported,
    )

    assert exported(ConfigurationModuleName.QOS) == "business_policy"
    assert type(exported(ConfigurationModuleName.DEVICE_SETTINGS)) is str
