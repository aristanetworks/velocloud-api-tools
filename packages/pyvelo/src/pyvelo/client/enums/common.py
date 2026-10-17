from enum import IntEnum


class PendingActionStatus(IntEnum):
    PENDING = 0
    NOTIFIED = 1
    COMPLETE = 2
    ERROR = 3
    TIMEDOUT = 4
    PENDING_DELETE = 5
    PENDING_LOCATION = 6
    ENQUEUED = 7
    PENDING_ACTIVATION = 8
