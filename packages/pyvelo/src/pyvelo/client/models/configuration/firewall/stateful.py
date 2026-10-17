from pyvelo.core import VcoModel


class StatefulFirewallSettings(VcoModel):
    established_tcp_flow_timeout: int = 7440
    non_established_tcp_flow_timeout: int = 240
    udp_flow_timeout: int = 300
    other_flow_timeout: int = 60
