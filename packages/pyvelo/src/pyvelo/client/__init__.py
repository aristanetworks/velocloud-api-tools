"""VeloCloud API client."""

from pyvelo.client.auth import VcoAuth
from pyvelo.client.credentials import get_vco_credentials
from pyvelo.client.exceptions import (
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
    "get_vco_credentials",
    "make_file_dump_hook",
]
