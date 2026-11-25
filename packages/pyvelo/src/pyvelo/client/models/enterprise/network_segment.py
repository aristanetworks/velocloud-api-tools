"""An enterprise network segment's data: its segment ID, delegation and service VLAN."""

from pyvelo.core import VcoModel


class NetworkSegmentData(VcoModel):
    segment_id: int
    delegate_to_enterprise: bool
    delegate_to_enterprise_proxy: bool
    service_vlan: int | None = None
