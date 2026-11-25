"""Payload-free accounting for one source request, including HTTP retries."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestObservation:
    """What one ``VcoClient`` request did, given to the ``request_observer``.

    It holds the request's method names, the attempts and retries it took,
    the time spent and the client's rate-limit settings, and never the
    payload or the token.
    """

    http_method: str
    endpoint: str
    rpc_method: str
    has_cursor: bool
    http_attempts: int
    http_429_count: int
    last_http_status: int | None
    outcome: str
    elapsed_seconds: float
    http_seconds: float
    limiter_wait_seconds: float
    retry_wait_seconds: float
    retry_delays: tuple[float, ...]
    retry_after_delays: tuple[float, ...]
    max_requests_per_window: int | None
    rate_limit_window_seconds: float
    max_retries: int


RequestObserver = Callable[[RequestObservation], None]
