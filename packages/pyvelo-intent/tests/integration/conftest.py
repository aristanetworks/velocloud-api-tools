"""Session fixtures for the live ConfigPipeline tests.

Connection details come from the environment, as in pyvelo's integration tests:

    VCO_HOST            required: VCO FQDN
    VCO_TOKEN           required: API token
    VCO_ENTERPRISE_ID   optional: only for a token that needs the enterprise named
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import live_support as live
import pytest
import pytest_asyncio
from live_support import Lab, discover_lab
from pyvelo.client import VcoClient
from pyvelo_intent import ConfigPipeline, build_default_pipeline


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Keep lab data out of the report of a failed live configuration test."""
    report = yield
    live.redact_report(item, call, report)
    return report


def _env(name: str, required: bool = False) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        pytest.skip(f"{name} not set")
    return val


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def vco_client() -> AsyncGenerator[VcoClient]:
    host = _env("VCO_HOST", required=True)
    token = _env("VCO_TOKEN", required=True)
    assert host is not None
    assert token is not None
    async with VcoClient(vco_fqdn=host, token=token) as client:
        yield client


@pytest.fixture(scope="session")
def config_enterprise_id() -> int | None:
    val = _env("VCO_ENTERPRISE_ID")
    return int(val) if val else None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def lab(vco_client: VcoClient, config_enterprise_id: int | None) -> Lab:
    """Every edge and profile the live pipeline tests can use, read once."""
    return await discover_lab(vco_client, config_enterprise_id)


@pytest.fixture
def pipeline(vco_client: VcoClient) -> ConfigPipeline:
    """A pipeline with the default resolvers; one per test."""
    return build_default_pipeline(vco_client)
