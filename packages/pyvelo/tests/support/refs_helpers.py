"""Builders for the tests of derived refs: segments, services, ref rows, modules, stores and registries.

``tests/support`` is on pytest's ``pythonpath`` and basedpyright's
``extraPaths``, so the tests of both packages import this as ``refs_helpers``.
"""

import copy
from typing import Any

from pydantic import MISSING
from pyvelo.client.schemas.configuration import DeviceSettingsConfigurationModule
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import ConfigContext
from pyvelo.core.identity import NetworkServiceIdentity, SegmentIdentity
from pyvelo.testing import ModuleStore, make_module_json, make_segment_resolver
from pyvelo.testing.fixtures import make_network_service_resolver

CONFIG = 100
DS_ID = 10
QOS_ID = 11

SEG0 = "11111111-1111-4111-8111-111111111111"
SEG0_OBJECT = 501
SEG1 = "22222222-2222-4222-8222-222222222222"
SEG1_OBJECT = 502

DNS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DNS_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
AUTH = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CSS = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

_PRIMARY = "deviceSettings:dns:primaryProvider"
_BACKUP = "deviceSettings:dns:backupProvider"


def segment_entry(
    segment_id: int, name: str, logical_id: str, **sections: Any
) -> dict[str, Any]:
    """A device settings segment entry as JSON; *sections* are its other keys."""
    return {
        "segment": {
            "segmentId": segment_id,
            "name": name,
            "type": "REGULAR",
            "segmentLogicalId": logical_id,
        },
        **sections,
    }


def dns(primary: bool = False, backup: bool = False) -> dict[str, Any]:
    """A ``dns`` section with a ``{"ref": ...}`` stub for each provider asked for; the others are empty."""
    return {
        "primaryProvider": {"ref": _PRIMARY} if primary else {},
        "backupProvider": {"ref": _BACKUP} if backup else {},
        "privateProviders": {},
    }


def segment_row(segment_id: int, object_id: int, logical_id: str) -> dict[str, Any]:
    """A complete ``deviceSettings:segment`` row as read."""
    return {
        "id": 9000 + object_id,
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": object_id,
        "ref": "deviceSettings:segment",
        "segmentObjectId": None,
        "logicalId": logical_id,
        "name": f"segment-{segment_id}",
        "type": "REGULAR",
        "object": "NETWORK_SEGMENT",
        "data": {"segmentId": segment_id},
    }


def service_row(
    name: str,
    logical_id: str,
    object_id: int,
    segment_object_id: int,
    segment_logical_id: str,
) -> dict[str, Any]:
    """A complete per-segment row as read, of ref *name*."""
    return {
        "id": 8000 + object_id,
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "enterpriseObjectId": object_id,
        "ref": name,
        "segmentObjectId": segment_object_id,
        "segmentLogicalId": segment_logical_id,
        "logicalId": logical_id,
        "name": f"service-{object_id}",
        "type": "dns",
        "object": "NETWORK_SERVICE",
        "data": {},
    }


def ds_module(
    segments: list[dict[str, Any]],
    refs: dict[str, Any] | MISSING,
    *,
    refs_loaded: bool = True,
    level: ConfigContext = ConfigContext.PROFILE,
    version: str = "v1",
) -> DeviceSettingsConfigurationModule:
    """A device settings module of configuration ``CONFIG`` with the given segments and refs.

    It is validated as a read is, at *level*, with *refs_loaded* as the
    validation context's flag.
    """
    data = {"lan": {"networks": [], "interfaces": []}, "segments": segments}
    row = make_module_json(
        "deviceSettings",
        copy.deepcopy(data),
        module_id=DS_ID,
        configuration_id=CONFIG,
        version=version,
        refs=copy.deepcopy(refs) if refs is not MISSING else MISSING,
    )
    return DeviceSettingsConfigurationModule.model_validate(
        row, context={"config_level": level, "refs_loaded": refs_loaded}
    )


def store_with_services() -> ModuleStore:
    """A store holding the four services and the two segment objects."""
    store = ModuleStore()
    store.add_object(701, logical_id=DNS_A, name="dns-a", type_="dns")
    store.add_object(702, logical_id=DNS_B, name="dns-b", type_="dns")
    store.add_object(703, logical_id=AUTH, name="radius-1", type_="authentication")
    store.add_object(704, logical_id=CSS, name="css-1", type_="cloudSecurityService")
    store.add_object(
        SEG0_OBJECT,
        logical_id=SEG0,
        name="Global Segment",
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
    )
    store.add_object(
        SEG1_OBJECT,
        logical_id=SEG1,
        name="Voice",
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
    )
    return store


async def table_registry(*identities: object) -> ResolverRegistry:
    """A registry whose segment and network-service resolvers are loaded with *identities*."""
    segments = [i for i in identities if isinstance(i, SegmentIdentity)]
    services = [i for i in identities if isinstance(i, NetworkServiceIdentity)]
    registry = ResolverRegistry()
    registry.register(
        await make_segment_resolver(
            [
                {
                    "name": s.name,
                    "segment_id": s.segment_id,
                    "object_id": s.object_id,
                    "segment_logical_id": s.logical_id,
                }
                for s in segments
            ]
        )
    )
    registry.register(await make_network_service_resolver(services))
    return registry
