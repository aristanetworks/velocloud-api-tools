from pyvelo.core import VcoModel


class NetworkSegmentData(VcoModel):
    segment_id: int
    delegate_to_enterprise: bool
    delegate_to_enterprise_proxy: bool
    service_vlan: int | None = None
