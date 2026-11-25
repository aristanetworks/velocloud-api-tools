"""Session-scoped fixtures for live VCO integration tests.

All connection details are injected via environment variables:

    VCO_HOST            required — VCO FQDN (e.g. vco123.velocloud.net)
    VCO_TOKEN           required — API token
    VCO_ENTERPRISE_ID   required — enterprise ID
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx
import live_support as live
import pytest
import pytest_asyncio
from live_support import Lab, discover_lab
from pyvelo.analytics import Analytics
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.config import ConfigPipeline, build_default_pipeline
from pyvelo.core import ApiInterval

from ._discovery import VcoTestContext, discover

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from pyvelo.client import VcoClient


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Keep lab data out of the report of a failed live configuration test."""
    report = yield
    live.redact_report(item, call, report)
    return report


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Name the backup of every module a live test did not put back."""
    live.report_leftover_backups(terminalreporter.write_line)


def _env(name: str, *, required: bool = False) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        pytest.skip(f"{name} not set")
    return val


@pytest.fixture(scope="session")
def interval() -> ApiInterval:
    now_ms = int(time.time() * 1000)
    return ApiInterval.model_validate(
        {"start": now_ms - 14 * 86_400_000, "end": now_ms - 3_600_000}
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def vco_client() -> AsyncGenerator[VcoClient]:
    async with live.connect() as client:
        yield client


@pytest.fixture(autouse=True)
def live_write_marker(request: pytest.FixtureRequest) -> Generator[None]:
    """Only a test marked ``live_write`` may write to the Orchestrator."""
    with live.writer_test(
        marked=request.node.get_closest_marker("live_write") is not None
    ):
        yield


@pytest.fixture(scope="session")
def enterprise_id() -> int:
    val = _env("VCO_ENTERPRISE_ID", required=True)
    assert val is not None
    return int(val)


@pytest.fixture(scope="session")
def config_enterprise_id() -> int | None:
    """The enterprise the configuration tests name in their requests, if one is set.

    Unlike the analytics tests, these do not need VCO_ENTERPRISE_ID: a token that
    belongs to one enterprise works without it.
    """
    val = _env("VCO_ENTERPRISE_ID")
    return int(val) if val else None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def lab(vco_client: VcoClient, config_enterprise_id: int | None) -> Lab:
    """Every edge and profile the live configuration tests can use, read once."""
    return await discover_lab(vco_client, config_enterprise_id)


@pytest.fixture
def pipeline(vco_client: VcoClient) -> ConfigPipeline:
    """A pipeline with the default resolvers; one per test."""
    return build_default_pipeline(vco_client)


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


def skip_unless(condition: bool, reason: str) -> pytest.MarkDecorator:  # noqa: FBT001 (mirrors pytest.mark.skipif, whose condition is positional)
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
