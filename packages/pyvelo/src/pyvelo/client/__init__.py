"""VeloCloud API client."""

from pyvelo.client.auth import VcoAuth
from pyvelo.client.credentials import get_vco_credentials
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    LossyFieldError,
    ModuleWriteError,
    UnboundError,
    VcoApiError,
    VcoClientNotInitializedError,
    VcoCredentialsError,
    VcoEmptyResultError,
    VcoError,
    VcoHttpError,
    VcoOperationError,
    VcoRateLimitError,
    VcoWebSocketConnectionError,
    VcoWebSocketDisconnectedError,
    VcoWebSocketError,
    VcoWebSocketTimeoutError,
    VcoWebSocketValidationError,
    WriteNotAppliedError,
)
from pyvelo.client.protocol import (
    Exchange,
    ExchangeHook,
    VcoClientProtocol,
    make_file_dump_hook,
)
from pyvelo.client.request_observation import RequestObservation, RequestObserver
from pyvelo.client.vco_client import VcoClient
from pyvelo.client.websocket import PendingDiagnostic, VcoWebSocket

__all__ = [
    "ConfigConflictError",
    "DetachedObjectError",
    "Exchange",
    "ExchangeHook",
    "LossyFieldError",
    "ModuleWriteError",
    "PendingDiagnostic",
    "RequestObservation",
    "RequestObserver",
    "UnboundError",
    "VcoApiError",
    "VcoAuth",
    "VcoClient",
    "VcoClientNotInitializedError",
    "VcoClientProtocol",
    "VcoCredentialsError",
    "VcoEmptyResultError",
    "VcoError",
    "VcoHttpError",
    "VcoOperationError",
    "VcoRateLimitError",
    "VcoWebSocket",
    "VcoWebSocketConnectionError",
    "VcoWebSocketDisconnectedError",
    "VcoWebSocketError",
    "VcoWebSocketTimeoutError",
    "VcoWebSocketValidationError",
    "WriteNotAppliedError",
    "get_vco_credentials",
    "make_file_dump_hook",
]
