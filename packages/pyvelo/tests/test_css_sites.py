"""The cloud security sites of an edge's segment, as a VCO stores them.

An IPsec site holds its credentials (``ikeId`` and ``psk``); a stored one may
lack them, and must still load and be sent back as it was read.
"""

import copy
import json
from typing import Any

from pydantic import MISSING
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import (
    CssGreSite,
    CssIpsecSite,
    CssIpsecSiteData,
)
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.core import ConfigContext, Key, Path, hide_secrets, is_secret
from pyvelo.testing import make_module_json

from ._type_support import require_present

EDGE = ConfigContext.EDGE
PSK = "PSK-made-up"


def u(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012x}"


ADDRESSING: dict[str, Any] = {
    "internalRouterIp": "172.17.0.1",
    "internalRouterMask": "255.255.255.252",
    "internalZenIp": "172.17.0.2",
    "internalZenMask": "255.255.255.252",
}
IPSEC: dict[str, Any] = {
    "logicalId": u(1),
    "data": {
        "ikeId": "edge@example.test",
        "ikeIdType": "FQDN",
        "psk": PSK,
        "pskType": "alpha",
    },
}
BARE: dict[str, Any] = {"logicalId": u(2), "data": {}}
GRE: dict[str, Any] = {
    "logicalId": u(3),
    "data": {
        "useCustomSourceIp": False,
        "customSourceIp": "",
        "linkInternalLogicalId": u(4),
        "primaryAddressing": ADDRESSING,
        "secondaryAddressing": ADDRESSING,
    },
}


def document(*sites: dict[str, Any]) -> dict[str, Any]:
    descriptor = {
        "segmentId": 0,
        "name": "Global Segment",
        "type": "REGULAR",
        "segmentLogicalId": u(0),
    }
    css = {"enabled": True, "override": True, "sites": list(sites)}
    return copy.deepcopy(
        {
            "lan": {"networks": [], "interfaces": []},
            "segments": [{"segment": descriptor, "css": css}],
        }
    )


def parsed(raw: dict[str, Any]) -> list[DeviceSettingsConfigurationModule]:
    """*raw* as a module row, parsed from python objects and from JSON text."""
    row = make_module_json("deviceSettings", copy.deepcopy(raw))
    cls = DeviceSettingsConfigurationModule
    return [
        cls.model_validate(copy.deepcopy(row)),
        cls.model_validate_json(json.dumps(row)),
    ]


def sites_of(module: DeviceSettingsConfigurationModule) -> list[Any]:
    return list(require_present(module.data.segments[0].css).sites)


def test_an_ipsec_site_without_its_credentials_loads_and_is_sent_as_read() -> None:
    raw = document(IPSEC, BARE)
    for module in parsed(raw):
        full, bare = sites_of(module)
        assert isinstance(full, CssIpsecSite)
        assert isinstance(bare, CssIpsecSite)
        assert (full.data.ike_id, full.data.psk) == ("edge@example.test", PSK)
        assert bare.data.ike_id is MISSING
        assert bare.data.psk is MISSING
        plan = plan_module(module, level=EDGE)
        assert (plan.operation, plan.data) == ("none", raw)


def test_an_edit_beside_the_sites_leaves_a_site_without_credentials_as_read() -> None:
    raw = document(IPSEC, BARE)
    (module, _) = parsed(raw)
    require_present(module.data.segments[0].css).enabled = False
    expected = copy.deepcopy(raw)
    expected["segments"][0]["css"]["enabled"] = False
    assert plan_module(module, level=EDGE).data == expected


def test_absent_credentials_are_left_out_of_a_dump_not_written_null() -> None:
    dumped = CssIpsecSiteData.model_validate({}).model_dump(mode="json", by_alias=True)
    assert dumped == {"ikeIdType": "FQDN", "pskType": "alpha"}


def test_sites_that_are_all_gre_are_still_read_as_gre() -> None:
    raw = document(GRE, {**GRE, "logicalId": u(5)})
    for module in parsed(raw):
        assert [type(site) for site in sites_of(module)] == [CssGreSite, CssGreSite]
        plan = plan_module(module, level=EDGE)
        assert (plan.operation, plan.data) == ("none", raw)


def test_the_psk_is_still_a_secret() -> None:
    assert is_secret(CssIpsecSiteData.model_fields["psk"])
    assert not is_secret(CssIpsecSiteData.model_fields["ike_id"])
    site = ("segments", Key("Global Segment"), "css", "sites", 0, "data")
    psk = Path((*site, "psk"))
    assert hide_secrets(DeviceSettingsData, psk, PSK) == "***"
    assert hide_secrets(DeviceSettingsData, psk, MISSING) is MISSING
    shown = hide_secrets(DeviceSettingsData, Path(site), {"ikeIdType": "FQDN"})
    assert shown == {"ikeIdType": "FQDN"}
