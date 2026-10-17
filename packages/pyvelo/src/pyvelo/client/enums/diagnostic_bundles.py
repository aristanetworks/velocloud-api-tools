from enum import IntEnum, StrEnum


class DiagnosticBundleStatus(IntEnum):
    PENDING = 0
    RECEIVED = 1
    COMPLETE = 2


class DiagnosticBundleType(StrEnum):
    DIAGNOSTIC_DUMP = "diagnosticDump"
    PACKET_CAPTURE = "packetCapture"
