from __future__ import annotations

from pydantic_core import MISSING

from pyvelo.core import VcoModel


class ManagementPlaneProxyDetail(VcoModel):
    ip_v4: str | MISSING = MISSING
    ip_v6: str | MISSING = MISSING
    fqdn: str | MISSING = MISSING


class ManagementPlaneProxy(VcoModel):
    primary: str
    secondary: str | None = None
    primary_detail: ManagementPlaneProxyDetail | MISSING = MISSING
    secondary_detail: ManagementPlaneProxyDetail | MISSING = MISSING


class ManagementPlaneData(VcoModel):
    heart_beat_seconds: int
    management_plane_proxy: ManagementPlaneProxy
    stats_upload_seconds: int
    time_slice_seconds: int
