"""VeloCloud API client."""

from pyvelo.client.auth import VcoAuth
from pyvelo.client.exceptions import (
    VcoApiError,
    VcoClientNotInitializedError,
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
    "Exchange",
    "ExchangeHook",
    "PendingDiagnostic",
    "RequestObservation",
    "RequestObserver",
    "VcoApiError",
    "VcoAuth",
    "VcoClient",
    "VcoClientNotInitializedError",
    "VcoClientProtocol",
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
    "make_file_dump_hook",
]
