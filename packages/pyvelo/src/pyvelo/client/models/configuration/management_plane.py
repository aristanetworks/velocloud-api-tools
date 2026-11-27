"""The ``managementPlane`` module's data.

The primary and secondary management plane proxies, and the heartbeat, stats upload
and time slice intervals.
"""

from __future__ import annotations

from pydantic import MISSING

from pyvelo.core import ConfigModel


class ManagementPlaneProxyDetail(ConfigModel):
    ip_v4: str | MISSING = MISSING
    ip_v6: str | MISSING = MISSING
    fqdn: str | MISSING = MISSING


class ManagementPlaneProxy(ConfigModel):
    primary: str
    secondary: str | None = None
    primary_detail: ManagementPlaneProxyDetail | MISSING = MISSING
    secondary_detail: ManagementPlaneProxyDetail | MISSING = MISSING


class ManagementPlaneData(ConfigModel):
    heart_beat_seconds: int
    management_plane_proxy: ManagementPlaneProxy
    stats_upload_seconds: int
    time_slice_seconds: int
