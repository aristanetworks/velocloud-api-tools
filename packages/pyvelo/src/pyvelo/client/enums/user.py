from enum import StrEnum


class UserType(StrEnum):
    """The type of user associated with a VCO API token."""

    OPERATOR = "OPERATOR"
    MSP = "MSP"
    ENTERPRISE = "ENTERPRISE"
