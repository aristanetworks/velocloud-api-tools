"""Core enums needed by identity types.

These live in ``pyvelo.core`` because :mod:`pyvelo.core.identity` references
them in identity dataclasses, and ``core`` must not depend on ``client``.
The same enums are re-exported from :mod:`pyvelo.client.enums.edge` and
:mod:`pyvelo.client.enums.enterprise` for ergonomics — both names refer to
the same enum class.
"""

from enum import StrEnum


class ModelNumber(StrEnum):
    EDGE500 = "edge500"
    EDGE5_X0 = "edge5X0"
    EDGE510 = "edge510"
    EDGE510LTE = "edge510lte"
    EDGE515 = "edge515"
    EDGE6_X0 = "edge6X0"
    EDGE610LTE = "edge610lte"
    ME6200 = "me6200"
    EDGE710 = "edge710"
    EDGE7105G = "edge7105g"
    EDGE7_X0 = "edge7X0"
    EDGE840 = "edge840"
    EDGE1000 = "edge1000"
    EDGE1000QAT = "edge1000qat"
    EDGE3_X00 = "edge3X00"
    EDGE3_X10 = "edge3X10"
    EDGE4100 = "edge4100"
    EDGE5100 = "edge5100"
    VIRTUAL = "virtual"


class NetworkSegmentType(StrEnum):
    REGULAR = "REGULAR"
    CDE = "CDE"
    PRIVATE = "PRIVATE"
