"""Session-scoped fixtures for live VCO integration tests.

All connection details are injected via environment variables:

    VCO_HOST            required — VCO FQDN (e.g. vco123.velocloud.net)
    VCO_API_TOKEN       required — API token
    VCO_ENTERPRISE_ID   required — enterprise ID
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pyvelo.analytics import Analytics
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client import VcoClient
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.core import ApiInterval

from ._discovery import VcoTestContext, discover


def _env(name: str, required: bool = False) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        pytest.skip(f"{name} not set")
    return val


def _env_int(name: str) -> int | None:
    val = _env(name)
    if not val:
        return None
    return int(val)


@pytest.fixture(scope="session")
def interval() -> ApiInterval:
    now_ms = int(time.time() * 1000)
    return ApiInterval.model_validate(
        {"start": now_ms - 14 * 86_400_000, "end": now_ms - 3_600_000}
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def vco_client():
    host = _env("VCO_HOST", required=True)
    token = _env("VCO_API_TOKEN", required=True)
    assert host is not None
    assert token is not None
    async with VcoClient(vco_fqdn=host, token=token) as client:
        yield client


@pytest.fixture(scope="session")
def enterprise_id() -> int:
    val = _env("VCO_ENTERPRISE_ID", required=True)
    assert val is not None
    return int(val)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ctx(vco_client: VcoClient, enterprise_id: int) -> VcoTestContext:
    return await discover(vco_client, enterprise_id)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def analytics(vco_client: VcoClient, enterprise_id: int) -> Analytics:
    return Analytics(vco_client, enterprise_id=enterprise_id)


def assert_frame_valid(frame: MetricFrame, *, check_other: bool = True) -> None:
    """Shared assertion: frame is a MetricFrame, no 'other' bucket rows leak through."""
    assert isinstance(frame, MetricFrame)
    if not check_other or "name" not in frame.columns:
        return
    for row in frame.rows:
        name = row.get("name")
        assert not (isinstance(name, str) and name.lower() == "other"), (
            f"'other' bucket row leaked through: {row}"
        )


def skip_unless(condition: Any, reason: str):
    return pytest.mark.skipif(not condition, reason=reason)


@contextmanager
def skip_on_vco_limitation() -> Generator[None]:
    try:
        yield
    except VcoOperationError as exc:
        msg = str(exc)
        if "does not have privilege" in msg:
            pytest.skip(f"Token lacks required privilege: {exc}")
        if "invalid enterprise context" in msg:
            pytest.skip(f"Requires operator context: {exc}")
        raise
    except httpx.ReadTimeout:
        pytest.skip("VCO request timed out")
