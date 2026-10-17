from pydantic import Field

from pyvelo.core import VcoModel


class TcpBasedAttacks(VcoModel):
    invalid_flags: bool = False
    enable_land: bool = False
    enable_syn_fragment: bool = False


class IcmpBasedAttacks(VcoModel):
    enable_ping_of_death: bool = False
    enable_fragment: bool = False


class IpBasedAttacks(VcoModel):
    enable_unknown_protocol: bool = False
    enable_insecure_options: bool = False


class NetworkProtectionSettings(VcoModel):
    denylist_duration: int = 10
    new_connection_threshold: int = 25
    denylist: bool = False
    detection_time: int = 10

    tcp_based_attacks_enabled: bool = False
    tcp_based_attacks: TcpBasedAttacks = Field(default_factory=TcpBasedAttacks)

    icmp_based_attacks_enabled: bool = False
    icmp_based_attacks: IcmpBasedAttacks = Field(default_factory=IcmpBasedAttacks)

    ip_based_attacks_enabled: bool = False
    ip_based_attacks: IpBasedAttacks = Field(default_factory=IpBasedAttacks)

    tcp_half_open_threshold_enabled: bool = False


class SecurityFeatureSettings(VcoModel):
    idps_enabled: bool = False
    url_filtering_enabled: bool = False
    malicious_ip_filtering_enabled: bool = False
