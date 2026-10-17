"""Payload-free accounting for one source request, including HTTP retries."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestObservation:
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
