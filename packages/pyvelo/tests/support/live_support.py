"""Support for the live configuration-save tests: reading, restoring and redacting.

The tests that use this write to a real Orchestrator, so everything here is
about leaving it as it was found. Nothing names a lab entity: targets are found
at run time, and every message refers to them by position and module type only.

Both live suites import this module by name, pyvelo's and pyvelo-intent's:
its directory is on ``pythonpath`` in the root ``pyproject.toml``. It is test
support and no part of the library.

A failed test reports what ``failure_text`` makes of its exception and where
it was raised, never the exception's own text: that quotes names and values
of the lab. ``check`` and ``pytest.fail`` word a failure for the report as it
is; a ``LiveSuiteError`` carries a message that is safe to show.

Running the live suite
----------------------

The suite is deselected by default. It reads the Orchestrator the library's
own variables name, ``VCO_HOST`` and ``VCO_TOKEN`` (and ``VCO_ENTERPRISE_ID``
when the token is not an enterprise's own). It writes only when two more
variables say that it may:

    PYVELO_IT_WRITE_HOST            the host name of the Orchestrator the suite
                                    may write to: the host of VCO_HOST, alone
    PYVELO_IT_WRITE_ENTERPRISE_ID   the ID of the enterprise it may write to:
                                    the token's own, or VCO_ENTERPRISE_ID

Both are checked against the client before anything is written: the host
against the one the client is connected to, the enterprise by asking the
Orchestrator which enterprise the run's requests address. Unset, the tests
that write are skipped and say so. Set and not matching, they fail, and
nothing is written. Set them for a lab, and never in a file that also serves
a production tenant.

    uv run --env-file .env pytest -m "integration and not live_write"   # reads only
    uv run --env-file .env pytest -m integration                        # everything

A test that writes is marked ``live_write``. The client refuses a write from
any other test, a write outside a ``restoring`` block, and a write to a
module no open block covers: the suite can only write what it will put back.

``restoring`` is the one way a test writes: it records a module's raw data first
(in a private file under ``$PYVELO_IT_BACKUP_DIR``, which must be outside the
repository, else a new temp directory; it holds no refs but a QoS module's, and
never a client-connector ref; it is removed once the module is back) and, however the test ends, puts it back and checks that it is back. A
module that did not exist is deleted; one that was deleted is inserted again.
If the restore fails, the message names the file; it never prints the data.

What the Orchestrator does to a module as it stores it, seen on a lab:

* It adds ``routeMapsVersion`` to a deviceSettings segment entry that lacks it
  on a write (``settle``). A comparison ignores that key where the module as it
  was before the write lacked it; the key is then accepted drift, and it stays
  in a module the suite has written.
* It re-derives the refs of a deviceSettings module from its data when the data
  changes: ref IDs are re-issued, and a ref whose object is gone is dropped (it
  cannot be written back). The refs of a firewall or WAN module are left as they
  were. A client-connector ref carries a login token that is new on every read.
  So the tests never write refs, and restore data only.
* It assigns ``ruleLogicalId`` to a rule added without one (the tests that add
  rules allow it).
* Some modules of a lab are not valid as they stand and refuse any write, even
  of their own data: ``pick_writable_edge`` skips those.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import pathlib
import re
import tempfile
import uuid
import weakref
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Generator,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, cast

import pytest
from pydantic import MISSING, RootModel, ValidationError
from pyvelo.client import (
    ConfigConflictError,
    VcoClient,
    VcoHttpError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import LossyFieldError, ModuleWriteError
from pyvelo.client.module_check import data_model
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleBase,
    ConfigurationModuleUpdate,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    GetConfiguration,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.client.schemas.edge import (
    GetEdgeConfigurationModules,
    GetEdgeConfigurationStack,
)
from pyvelo.client.schemas.enterprise import GetEnterprise, GetEnterpriseEdges
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.core import (
    Alias,
    ConfigContext,
    NoneIsMissing,
    PatchError,
    RowsModifiedConfirmation,
    VcoModel,
)
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo.core.response_check import PlannedChange, ServerChange

SYNTHETIC_PREFIX = "pyvelo-it-"
"""Every rule, description and the like these tests add starts with this."""


def synthetic_name() -> str:
    return f"{SYNTHETIC_PREFIX}{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(path: Path) -> str:
    """*path* with every list key hidden but the synthetic names the tests add.

    Keys of keyed lists are the names of segments, rules, networks and links of
    the lab; they must not reach a test report.
    """
    out = ""
    for part in path.parts:
        if isinstance(part, Key):
            value = part.value
            shown = (
                value
                if isinstance(value, str) and value.startswith(SYNTHETIC_PREFIX)
                else "*"
            )
            out += f"[{shown}]"
        elif isinstance(part, str):
            out += f".{part}" if out else part
        else:
            out += f"[#{part}]"
    return out or "<root>"


def describe(changes: Sequence[RawChange]) -> str:
    """The changes as ``+ added``, ``- removed`` and ``~ changed`` redacted paths."""
    marks = [
        f"{'+' if c.before is MISSING else '-' if c.after is MISSING else '~'} {redact(c.path)}"
        for c in changes
    ]
    return "; ".join(marks) if marks else "no changes"


def check(condition: bool, message: str) -> None:
    """Fail with *message* alone (an ``assert`` would print the operands, names included)."""
    if not condition:
        pytest.fail(message, pytrace=False)


def expect[T](value: object, kind: type[T], what: str) -> T:
    """*value* as a *kind*; fail with *what* alone (an ``isinstance`` assert prints the value)."""
    if not isinstance(value, kind):
        pytest.fail(f"{what} is a {type(value).__name__}", pytrace=False)
    return value


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EdgeLayout:
    """One edge of the lab as it was found: IDs for the tests, no names."""

    index: int
    edge_id: int
    configuration_id: int
    profile_id: int
    edge: EdgeConfiguration
    profile: EnterpriseConfigurationProfile

    def has(self, name: ConfigurationModuleName) -> bool:
        return find_module(self.edge, name) is not None


def find_module(
    config: ConfigurationBase,
    name: ConfigurationModuleName | str,
) -> AnyConfigurationModule | None:
    if config.modules is MISSING:
        return None
    return next((m for m in config.modules if m.name == name), None)


async def read_stack(
    client: VcoClient, edge_id: int, enterprise_id: int | None
) -> tuple[EdgeConfiguration, EnterpriseConfigurationProfile]:
    """The edge's own configuration, then the profile it inherits from."""
    request = GetEdgeConfigurationStack(edge_id=edge_id)
    if enterprise_id is not None:
        request.enterprise_id = enterprise_id
    stack = await client.get_edge_configuration_stack(request)
    return stack[0], stack[1]


async def read_profile(
    client: VcoClient, profile_id: int, enterprise_id: int | None
) -> ConfigurationBase:
    request = GetConfiguration(id_=profile_id, with_=[GetConfigurationWith.MODULES])
    if enterprise_id is not None:
        request.enterprise_id = enterprise_id
    return await client.get_configuration(request)


LAB_LIMIT = 50
"""At most this many edges, and this many profiles, are read and swept."""


async def list_edge_ids(client: VcoClient, enterprise_id: int | None) -> list[int]:
    edges = await client.get_enterprise_edges(
        GetEnterpriseEdges(enterprise_id=enterprise_id if enterprise_id else MISSING)
    )
    return [e.id_ for e in edges if isinstance(e.id_, int)][:LAB_LIMIT]


async def list_profile_ids(client: VcoClient, enterprise_id: int | None) -> list[int]:
    profiles = await client.get_enterprise_configurations(
        GetEnterpriseConfigurations(
            enterprise_id=enterprise_id if enterprise_id else MISSING
        )
    )
    return [p.id_ for p in profiles if isinstance(p.id_, int)][:LAB_LIMIT]


class LiveSuiteError(Exception):
    """The base of the suite's own errors: a message of one names no entity of the lab."""


class LabUnreadable(LiveSuiteError):
    """An edge of the lab does not load; the message names no entity."""


def _unreadable(index: int, exc: ValidationError) -> LabUnreadable:
    """A ``ValidationError`` as the error types alone: its input is lab data."""
    kinds = sorted({str(error["type"]) for error in exc.errors()})
    return LabUnreadable(
        f"edge #{index} does not load: {exc.error_count()} validation error(s) "
        f"of type {', '.join(kinds)}"
    )


async def discover_layouts(
    client: VcoClient, enterprise_id: int | None
) -> list[EdgeLayout]:
    """Every edge, with its configuration stack as the models read it.

    An edge that does not load raises ``LabUnreadable``: a lab that this suite
    cannot read is not a lab it can test.
    """
    layouts: list[EdgeLayout] = []
    for index, edge_id in enumerate(await list_edge_ids(client, enterprise_id)):
        try:
            edge, profile = await read_stack(client, edge_id, enterprise_id)
        except ValidationError as exc:
            raise _unreadable(index, exc) from None
        if not isinstance(edge.id_, int) or not isinstance(profile.id_, int):
            continue
        layouts.append(
            EdgeLayout(
                index=index,
                edge_id=edge_id,
                configuration_id=edge.id_,
                profile_id=profile.id_,
                edge=edge,
                profile=profile,
            )
        )
    return layouts


@dataclass(frozen=True)
class Lab:
    """What the Orchestrator has, as found by one read of every edge and profile."""

    client: VcoClient
    enterprise_id: int | None
    layouts: list[EdgeLayout]
    profile_ids: list[int]

    def edge_site(self, layout: EdgeLayout, name: ConfigurationModuleName) -> Site:
        return edge_site(self.client, layout, name, self.enterprise_id)

    def profile_site(self, profile_id: int, name: ConfigurationModuleName) -> Site:
        return profile_site(self.client, profile_id, name, self.enterprise_id)

    def edges_with(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyConfigurationModule], bool] = lambda _: True,
    ) -> list[EdgeLayout]:
        """The edges whose own configuration has a *name* module that is *suitable*."""
        found: list[EdgeLayout] = []
        for layout in self.layouts:
            module = find_module(layout.edge, name)
            if module is not None and suitable(module):
                found.append(layout)
        return found

    async def first_writable(
        self, layouts: Sequence[EdgeLayout], name: ConfigurationModuleName
    ) -> EdgeLayout | None:
        """The first of *layouts* whose *name* module takes a write."""
        require_writes(run_of(self.client))
        for layout in layouts:
            if await accepts_a_write(self.edge_site(layout, name)):
                return layout
        return None

    async def pick_writable_edge(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyConfigurationModule], bool] = lambda _: True,
        what: str = "",
    ) -> EdgeLayout:
        """The first such edge whose module takes a write, or skip the test."""
        require_writes(run_of(self.client))
        found = self.edges_with(name, suitable)
        if not found:
            pytest.skip(f"no edge has a {name.value} module {what or 'to use'}")
        layout = await self.first_writable(found, name)
        if layout is None:
            pytest.skip(f"no edge has a {name.value} module that takes a write")
        return layout

    async def pick_writable_profile(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyConfigurationModule], bool] = lambda _: True,
        what: str = "",
    ) -> int:
        """The ID of the first profile with such a module that takes a write, or skip."""
        require_writes(run_of(self.client))
        found = 0
        for profile_id in self.profile_ids:
            config = await read_profile(self.client, profile_id, self.enterprise_id)
            module = find_module(config, name)
            if module is None or not suitable(module):
                continue
            found += 1
            if await accepts_a_write(self.profile_site(profile_id, name)):
                return profile_id
        if found:
            pytest.skip(f"no profile has a {name.value} module that takes a write")
        pytest.skip(f"no profile has a {name.value} module {what or 'to use'}")


def _shown_field(path: str) -> str:
    """*path* of a refusal with every bracket but an index hidden: names in brackets are the lab's."""
    shown = re.sub(
        r"\[([^\]]*)\]", lambda m: m.group(0) if m.group(1).isdigit() else "[*]", path
    )
    return shown if re.fullmatch(r"[\w.\[\]*]*", shown) else "*"


def _found_codes(exc: VcoOperationError) -> list[tuple[str, str]]:
    """The code and the field of each error in the refusal's data, and nothing of its text or messages.

    A refusal with no such data has none: its text can hold names of the lab in
    any form, brackets included.
    """
    data: object = exc.data
    if not isinstance(data, dict):
        return []
    entries = cast(dict[str, object], data).get("error")
    if not isinstance(entries, list):
        return []
    found: list[tuple[str, str]] = []
    for entry in cast(list[object], entries):
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, object], entry)
        code, path = fields.get("code"), fields.get("path")
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
            found.append((code, _shown_field(path) if isinstance(path, str) else ""))
    return found


def _redacted(exc: VcoOperationError) -> VcoOperationError:
    """*exc* with each error code and field kept and no message; ``refusal_codes`` reads it as it reads *exc*."""
    errors = [
        {"code": code, "path": field, "message": ""}
        for code, field in _found_codes(exc)
    ]
    return VcoOperationError(
        exc.operation, "refused", code=exc.code, data={"error": errors}
    )


def refusal_codes(exc: VcoOperationError) -> str:
    """The error codes and fields of a refusal, from its data; its text and messages name lab entities."""
    found = _found_codes(exc)
    return (
        "; ".join(f"{code} {field}".strip() for code, field in found) or "no error code"
    )


# ---------------------------------------------------------------------------
# Writing: the opt-in and the guard
# ---------------------------------------------------------------------------

WRITE_HOST_ENV = "PYVELO_IT_WRITE_HOST"
WRITE_ENTERPRISE_ENV = "PYVELO_IT_WRITE_ENTERPRISE_ID"

WRITES_OFF = (
    "live write tests are off: set PYVELO_IT_WRITE_HOST to the host name of the "
    "Orchestrator this suite may write to and PYVELO_IT_WRITE_ENTERPRISE_ID to the "
    "ID of the enterprise, then run again"
)
NOT_MARKED = (
    "this test writes to the Orchestrator and is not marked live_write; "
    "nothing was written; mark the test with @pytest.mark.live_write"
)
OUTSIDE_RESTORING = (
    "a write outside a restoring block; nothing was sent; "
    "make the write inside an `async with restoring(site)` block"
)
NOT_COVERED = (
    "that no open restoring block covers; nothing was sent; open "
    "`restoring(site)` on the module's own site and write inside it"
)


class WritesRefused(LiveSuiteError):
    """A write the suite will not send; the message says why and names no entity."""


@dataclass(frozen=True)
class WriteAccess:
    """Whether this run may write, and the refusal to show when it may not."""

    granted: bool = False
    refusal: str = WRITES_OFF
    misconfigured: bool = False
    """The opt-in is set and wrong: the test fails, where an unset one skips it."""


class EnterpriseId(VcoModel):
    """The one field of an enterprise that the opt-in is checked against."""

    id_: Annotated[int, Alias("id")]


_HOST = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


def _host_name(host: str) -> str:
    """*host* without white space or a last dot, in lower case."""
    return host.strip().removesuffix(".").lower()


def _refused(reason: str, remedy: str) -> WriteAccess:
    return WriteAccess(
        False, f"writes refused: {reason}; nothing was written; {remedy}", True
    )


async def check_write_access(
    client: VcoClient,
    enterprise_id: int | None,
    environ: Mapping[str, str] | None = None,
) -> WriteAccess:
    """Whether the two opt-in variables name the host and the enterprise of *client*.

    The host is compared with the one the client is connected to; the
    enterprise by asking the Orchestrator which enterprise the requests with
    *enterprise_id* address. Nothing is asked before the host matches. No
    message holds the host, the token or an ID.
    """
    variables = os.environ if environ is None else environ
    host = _host_name(variables.get(WRITE_HOST_ENV, ""))
    enterprise = variables.get(WRITE_ENTERPRISE_ENV, "").strip()
    if not host and not enterprise:
        return WriteAccess()
    if not host or not enterprise:
        return _refused(
            f"{WRITE_HOST_ENV} and {WRITE_ENTERPRISE_ENV} must both be set",
            "set the one that is missing, or unset the other to turn writes off",
        )
    if not re.fullmatch(r"[0-9]+", enterprise):
        return _refused(
            f"{WRITE_ENTERPRISE_ENV} is not a whole number",
            "set it to the ID of the enterprise, in digits",
        )
    if not _HOST.fullmatch(host):
        return _refused(
            f"{WRITE_HOST_ENV} must be a host name alone, with no scheme, port, "
            "path or user",
            "set it to the host name VCO_HOST names, and nothing else",
        )
    connected = _host_name(client._http_client().base_url.host)  # pyright: ignore[reportPrivateUsage]
    if host != connected:
        return _refused(
            f"{WRITE_HOST_ENV} does not name the Orchestrator this run is connected to",
            "set it to the host name VCO_HOST names, or point VCO_HOST at the "
            "Orchestrator you mean to write to",
        )
    try:
        addressed = await client.portal(
            "enterprise/getEnterprise",
            GetEnterprise(
                enterprise_id=enterprise_id if enterprise_id is not None else MISSING
            ),
            EnterpriseId,
        )
    except VcoOperationError as exc:
        return _refused(
            "the enterprise this run addresses could not be read from the "
            f"Orchestrator ({refusal_codes(exc)})",
            "set VCO_ENTERPRISE_ID if the token is not an enterprise's own",
        )
    except ValidationError:
        return _refused(
            "the enterprise this run addresses could not be read from the "
            "Orchestrator (the answer has no ID)",
            "check that VCO_HOST names an Orchestrator and that the token may "
            "read its enterprise",
        )
    if addressed.id_ != int(enterprise):
        return _refused(
            f"{WRITE_ENTERPRISE_ENV} is not the enterprise this run addresses",
            "set it to the ID of the enterprise the token belongs to, or that "
            "VCO_ENTERPRISE_ID names",
        )
    return WriteAccess(granted=True, refusal="")


_marked: bool = False
"""Whether the test now running is marked ``live_write``; false outside ``writer_test``."""


@contextmanager
def writer_test(marked: bool) -> Generator[None]:
    """For the length of the block, the test running is a writer (*marked*) or is not."""
    global _marked
    before = _marked
    _marked = marked
    try:
        yield
    finally:
        _marked = before


@dataclass
class LiveRun:
    """What one client may write, and what it has open to write: the guard's state."""

    access: WriteAccess = field(default_factory=WriteAccess)
    open: list[tuple[Site, int | None]] = field(
        default_factory=lambda: list[tuple[Site, int | None]]()
    )
    """The site of each open ``restoring`` block, and the ID of the module it found."""
    inserted: set[int] = field(default_factory=set[int])
    """The IDs of the modules this run inserted and has not deleted."""

    def _check_may_write(self) -> None:
        if not _marked:
            raise WritesRefused(NOT_MARKED)
        if not self.access.granted:
            raise WritesRefused(self.access.refusal)
        if not self.open:
            raise WritesRefused(OUTSIDE_RESTORING)

    def check_update(self, module_id: int) -> None:
        """Raise ``WritesRefused`` unless the update of *module_id* is one a block will put back."""
        self._check_may_write()
        found = {module for _, module in self.open if module is not None}
        if module_id not in found and module_id not in self.inserted:
            raise WritesRefused(f"an update of a module {NOT_COVERED}")

    def check_replace(self, configuration_id: int, name: str, what: str) -> None:
        """Raise ``WritesRefused`` unless an open block is on that configuration's *name* module.

        *what* is ``"an insert"`` or ``"a delete"``.
        """
        self._check_may_write()
        if not any(
            site.configuration_id == configuration_id and site.name.value == name
            for site, _ in self.open
        ):
            raise WritesRefused(f"{what} of a {name} module {NOT_COVERED}")


def require_writes(run: LiveRun) -> None:
    """Fail where the test may not write, skip where this run may not, else return."""
    if not _marked:
        pytest.fail(NOT_MARKED, pytrace=False)
    if run.access.misconfigured:
        pytest.fail(run.access.refusal, pytrace=False)
    if not run.access.granted:
        pytest.skip(run.access.refusal)


_RUNS: weakref.WeakKeyDictionary[VcoClient, LiveRun] = weakref.WeakKeyDictionary()


def run_of(client: VcoClient) -> LiveRun:
    """The run of *client*, made on the first call, which also puts the guard on the client.

    The guard is in the client's own write methods, so a test that forgets
    every helper is refused too. A refused write carries its codes alone: the
    Orchestrator's validation text quotes the names of what it refuses, and a
    refused write in a test would put them in the test output.
    """
    run = _RUNS.get(client)
    if run is not None:
        return run
    run = LiveRun()
    _RUNS[client] = run
    update = client.update_configuration_module
    insert = client.insert_configuration_module

    async def guarded_update(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyConfigurationModule | None:
        run.check_update(request.configuration_module_id)
        try:
            return await update(request, level=level)
        except VcoOperationError as exc:
            raise _redacted(exc) from None

    async def guarded_insert(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> InsertConfigurationModuleResult | AnyConfigurationModule:
        run.check_replace(request.configuration_id, request.name, "an insert")
        try:
            inserted = await insert(request, level=level)
        except VcoOperationError as exc:
            raise _redacted(exc) from None
        run.inserted.add(inserted.id_)
        return inserted

    # Instance attributes, which shadow the methods: a spy a test puts over them
    # (``monkeypatch.setattr``) wraps these.
    attributes = vars(client)
    attributes["update_configuration_module"] = guarded_update
    attributes["insert_configuration_module"] = guarded_insert
    return run


@asynccontextmanager
async def connect() -> AsyncGenerator[VcoClient]:
    """A client for the Orchestrator ``VCO_HOST`` and ``VCO_TOKEN`` name, with the write guard on it."""
    host = os.environ.get("VCO_HOST")
    if not host:
        pytest.skip("VCO_HOST not set")
    token = os.environ.get("VCO_TOKEN")
    if not token:
        pytest.skip("VCO_TOKEN not set")
    async with VcoClient(vco_fqdn=host, token=token) as client:
        _ = run_of(client)
        yield client


async def discover_lab(client: VcoClient, enterprise_id: int | None) -> Lab:
    run_of(client).access = await check_write_access(client, enterprise_id)
    return Lab(
        client=client,
        enterprise_id=enterprise_id,
        layouts=await discover_layouts(client, enterprise_id),
        profile_ids=await list_profile_ids(client, enterprise_id),
    )


# ---------------------------------------------------------------------------
# Sites: where a module lives, and how to read it again
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One module's place on the Orchestrator, with a fresh read of its layer."""

    client: VcoClient
    enterprise_id: int | None
    configuration_id: int
    name: ConfigurationModuleName
    layer: Callable[[], Awaitable[Sequence[AnyConfigurationModule]]]

    async def read(self) -> AnyConfigurationModule | None:
        modules = await self.layer()
        return next((m for m in modules if m.name == self.name), None)

    async def read_present(self) -> AnyConfigurationModule:
        module = await self.read()
        check(module is not None, f"the {self.name.value} module is gone")
        assert module is not None
        return module


def edge_site(
    client: VcoClient,
    layout: EdgeLayout,
    name: ConfigurationModuleName,
    enterprise_id: int | None,
) -> Site:
    async def layer() -> Sequence[AnyConfigurationModule]:
        edge, _ = await read_stack(client, layout.edge_id, enterprise_id)
        return [] if edge.modules is MISSING else edge.modules

    return Site(client, enterprise_id, layout.configuration_id, name, layer)


def profile_site(
    client: VcoClient,
    profile_id: int,
    name: ConfigurationModuleName,
    enterprise_id: int | None,
) -> Site:
    async def layer() -> Sequence[AnyConfigurationModule]:
        profile = await read_profile(client, profile_id, enterprise_id)
        return [] if profile.modules is MISSING else profile.modules

    return Site(client, enterprise_id, profile_id, name, layer)


RawJson = RootModel[dict[str, Any]]


async def effective_data(
    client: VcoClient,
    edge_id: int,
    name: ConfigurationModuleName,
    enterprise_id: int | None,
) -> dict[str, Any]:
    """The module's data as the Orchestrator composes it for the edge, as JSON.

    ``edge/getEdgeConfigurationModules`` merges the profile's module with the
    edge's, in the format the edge receives. Read raw, so that a key the models
    do not know is compared too. It is the one view of what an edge inherits.
    """
    request = GetEdgeConfigurationModules(edge_id=edge_id, modules=[name])
    if enterprise_id is not None:
        request.enterprise_id = enterprise_id
    try:
        result = await client.portal(
            "edge/getEdgeConfigurationModules", request, RawJson
        )
    except VcoOperationError as exc:
        raise _redacted(exc) from None
    module = expect(result.root[name.value], dict, f"the effective {name.value} module")
    return expect(module["data"], dict, f"the effective {name.value} data")


# ---------------------------------------------------------------------------
# Writing back what a test changed
# ---------------------------------------------------------------------------


class DeleteConfigurationModule(VcoModel):
    """``configuration/deleteConfigurationModule``; the library has no wrapper for it."""

    id_: Annotated[int, Alias("id")]
    enterprise_id: Annotated[int | MISSING, NoneIsMissing] = MISSING


async def delete_module(site: Site, module_id: int) -> None:
    run = run_of(site.client)
    run.check_replace(site.configuration_id, site.name.value, "a delete")
    request = DeleteConfigurationModule(id_=module_id)
    if site.enterprise_id is not None:
        request.enterprise_id = site.enterprise_id
    try:
        result = await site.client.portal(
            "configuration/deleteConfigurationModule", request, RowsModifiedConfirmation
        )
    except VcoOperationError as exc:
        raise _redacted(exc) from None
    check(not result.error, f"deleting the {site.name.value} module was refused")
    run.inserted.discard(module_id)


async def insert_module(site: Site, data: object, refs: object) -> None:
    """Insert a module (firewall or QoS only) with *data* exactly as given."""
    data = expect(data, dict, "the data to insert")
    enterprise = site.enterprise_id if site.enterprise_id is not None else MISSING
    if site.name is ConfigurationModuleName.FIREWALL:
        _ = await site.client.insert_configuration_module(
            InsertFirewallConfigurationModule(
                configuration_id=site.configuration_id,
                data=copy.deepcopy(data),
                enterprise_id=enterprise,
            )
        )
    elif site.name is ConfigurationModuleName.QOS:
        _ = await site.client.insert_configuration_module(
            InsertQosConfigurationModule(
                configuration_id=site.configuration_id,
                data=copy.deepcopy(data),
                refs=copy.deepcopy(refs) if isinstance(refs, dict) else None,  # pyright: ignore[reportUnknownArgumentType]
                enterprise_id=enterprise,
            )
        )
    else:
        raise AssertionError(f"a {site.name.value} module cannot be inserted")


@dataclass(frozen=True)
class Original:
    """A module as the test found it."""

    module_id: int
    data: object
    refs: object
    """Only to insert the module again if a test deleted it."""


SEGMENT_DRIFT_KEY = "routeMapsVersion"
"""Added by the Orchestrator to a deviceSettings segment entry, on a write."""


def _segment_key(entry: object) -> object:
    if isinstance(entry, dict):
        descriptor = cast(dict[str, object], entry).get("segment")
        if isinstance(descriptor, dict):
            return cast(dict[str, object], descriptor).get("segmentId")
    return None


def settle(name: ConfigurationModuleName, before: object, after: object) -> object:
    """*after*, less the keys the Orchestrator added on a write to what *before* was.

    Only ``routeMapsVersion`` on a deviceSettings segment entry that did not have
    it (or is new): accepted drift. Anything else, here or elsewhere, stays.
    """
    if (
        name is not ConfigurationModuleName.DEVICE_SETTINGS
        or not isinstance(before, dict)
        or not isinstance(after, dict)
    ):
        return after
    settled = copy.deepcopy(cast(dict[str, Any], after))
    had = {
        _segment_key(entry): SEGMENT_DRIFT_KEY in cast(dict[str, object], entry)
        for entry in cast(dict[str, Any], before).get("segments") or []
        if isinstance(entry, dict)
    }
    for entry in settled.get("segments") or []:
        if isinstance(entry, dict) and not had.get(_segment_key(entry), False):
            _ = cast(dict[str, object], entry).pop(SEGMENT_DRIFT_KEY, None)
    return settled


def changes_between(
    name: ConfigurationModuleName,
    before: object,
    after: object,
    model: type[VcoModel] | None = None,
) -> list[RawChange]:
    """``raw_changes`` from *before* to *after*, without the accepted drift (``settle``)."""
    return raw_changes(before, settle(name, before, after), model)


def has_key(value: object, key: str) -> bool:
    """Whether *key* is a key of *value* or of anything inside it."""
    if isinstance(value, dict):
        return any(
            k == key or has_key(v, key)
            for k, v in cast(dict[str, object], value).items()
        )
    if isinstance(value, list):
        return any(has_key(v, key) for v in cast(list[object], value))
    return False


OVERRIDE_KEY = "outboundOverride"
"""Not a key of the VCO or the edge: no firewall module may be sent or read with it."""


def check_no_override_flag(
    label: str, before: object, payload: object, read_back: object
) -> None:
    """No firewall payload and no read-back carries the key; say whose it is if one does.

    *before* is the module as it was read before the write (None if there was
    none). A key it already had is a stray of the lab's data (an older library
    wrote it): a write drops it, so the payload and the read-back must not have
    it either way, but the message tells which case it is.
    """
    had = before is not None and has_key(before, OVERRIDE_KEY)
    origin = (
        "the module already had it before the write (it was not dropped)"
        if had
        else "the module did not have it before the write (the write added it)"
    )
    check(
        not has_key(payload, OVERRIDE_KEY),
        f"{label}: the firewall payload carries {OVERRIDE_KEY}; {origin}",
    )
    check(
        not has_key(read_back, OVERRIDE_KEY),
        f"{label}: the firewall as read back carries {OVERRIDE_KEY}; {origin}",
    )


# -- a copy of the original, outside the repository ---------------------------

BACKUP_DIR_ENV = "PYVELO_IT_BACKUP_DIR"
_backup_dir: pathlib.Path | None = None


def _repository_root() -> pathlib.Path | None:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _backups() -> pathlib.Path:
    """A private directory for backups: ``$PYVELO_IT_BACKUP_DIR`` or a new temp one.

    A backup holds lab configuration, so the directory must be outside the
    repository: one inside it is refused.
    """
    global _backup_dir
    if _backup_dir is None:
        root = os.environ.get(BACKUP_DIR_ENV)
        if root:
            chosen = pathlib.Path(root).resolve()
            repository = _repository_root()
            if repository is not None and chosen.is_relative_to(repository):
                raise RuntimeError(f"{BACKUP_DIR_ENV} must be outside the repository")
            _backup_dir = chosen
            _backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            _backup_dir = pathlib.Path(tempfile.mkdtemp(prefix="pyvelo-it-backups-"))

            def remove_if_empty(path: pathlib.Path = _backup_dir) -> None:
                try:
                    path.rmdir()
                except OSError:
                    pass  # a backup is still there: that is the point of it

            _ = atexit.register(remove_if_empty)
    return _backup_dir


def _backup_refs(name: ConfigurationModuleName, refs: object) -> object:
    """The refs a restore may need: only a QoS insert takes any, and none is a client connector's.

    The client-connector ref carries a login token, so it is never stored.
    """
    if name is not ConfigurationModuleName.QOS or not isinstance(refs, dict):
        return None
    return {
        key: value
        for key, value in cast(dict[str, object], refs).items()
        if "clientConnector" not in key
    }


def save_backup(site: Site, original: Original) -> pathlib.Path:
    """Write *original* to a private file, so that a restore that fails is not the end of it."""
    path = _backups() / f"{uuid.uuid4().hex}-{site.name.value}.json"
    document = {
        "module": site.name.value,
        "configuration_id": site.configuration_id,
        "module_id": original.module_id,
        "data": original.data,
        "refs": _backup_refs(site.name, original.refs),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(document, handle)
    return path


async def capture(site: Site) -> Original | None:
    """The raw data and refs of the module now, or None if the layer lacks it."""
    module = await site.read()
    if module is None:
        return None
    return Original(
        module_id=module.id_,
        data=copy.deepcopy(module.raw_data),
        refs=copy.deepcopy(module.raw_refs),
    )


async def write_data(site: Site, module: AnyConfigurationModule, data: object) -> None:
    """Send *data* to the module as it stands (its version), with no refs."""
    document = expect(data, dict, "the data to write")
    _ = await site.client.update_configuration_module(
        UpdateConfigurationModule(
            configuration_module_id=module.id_,
            enterprise_id=(
                site.enterprise_id if site.enterprise_id is not None else MISSING
            ),
            version=module.version or MISSING,
            update=ConfigurationModuleUpdate(data=copy.deepcopy(document)),
        )
    )


async def restore(site: Site, original: Original | None) -> None:
    """Put the module back as *original* and check that it is.

    The raw data goes back as written, under the module's current version. A
    module that was absent is deleted; one that was deleted is inserted again.
    """
    current = await site.read()
    if original is None:
        if current is not None:
            await delete_module(site, current.id_)
        check(
            await site.read() is None,
            f"restore: the {site.name.value} module the test added is still there",
        )
        return
    if current is None:
        await insert_module(site, original.data, original.refs)
    elif changes_between(site.name, original.data, current.raw_data):
        await write_data(site, current, original.data)
    after = await site.read()
    check(after is not None, f"restore: the {site.name.value} module is missing")
    assert after is not None
    changes = changes_between(site.name, original.data, after.raw_data)
    check(
        not changes,
        f"restore: the {site.name.value} module differs from the original after "
        f"writing it back: {describe(changes)}",
    )


def ref_names(refs: object) -> list[str]:
    """The names of the refs; their content (IDs, login tokens) is not stable."""
    return sorted(cast(dict[str, object], refs)) if isinstance(refs, dict) else []


@asynccontextmanager
async def restoring(site: Site) -> AsyncGenerator[Original | None]:
    """Run a test's writes to *site*'s module and, however they end, undo them.

    The block is open for the client's write guard from the backup until the
    restore is done, so that the restore's own writes are covered.
    """
    run = run_of(site.client)
    require_writes(run)
    original = await capture(site)
    backup = save_backup(site, original) if original is not None else None
    entry = (site, original.module_id if original is not None else None)
    run.open.append(entry)
    try:
        try:
            yield original
        finally:
            try:
                await restore(site, original)
            except (Exception, pytest.fail.Exception) as exc:
                if backup is None:
                    raise
                what = (
                    str(exc)
                    if isinstance(exc, (pytest.fail.Exception, LiveSuiteError))
                    else type(exc).__name__
                )
                pytest.fail(
                    f"{what}; the original {site.name.value} module is saved at "
                    f"{backup}",
                    pytrace=False,
                )
            else:
                if backup is not None:
                    backup.unlink(missing_ok=True)
    finally:
        run.open.remove(entry)


async def accepts_a_write(site: Site) -> bool:
    """Whether the Orchestrator takes the module's own data back.

    Some modules of a lab are not valid as they stand (an address missing from a
    LAN network, say) and every write to them is refused, whatever it holds.

    The write is not free: the Orchestrator may add ``routeMapsVersion`` to a
    deviceSettings segment entry of a module it takes (``settle``). That key is
    accepted drift: it stays, and ``restore`` does not count it.
    """
    async with restoring(site) as original:
        module = await site.read()
        if original is None or module is None:
            return False
        try:
            await write_data(site, module, original.data)
        except VcoOperationError:
            return False
    return True


# ---------------------------------------------------------------------------
# What a test reports of a write
# ---------------------------------------------------------------------------


SERVER_ASSIGNED_KEYS = frozenset({"ruleLogicalId"})
"""Keys the Orchestrator adds to a new item; an added item is compared without them."""


def where(changes: Iterable[PlannedChange | ServerChange]) -> str:
    """The paths of *changes*, redacted: ``refs`` before a refs entry, ``order of`` before the order entry of a list."""

    def shown(change: PlannedChange | ServerChange) -> str:
        if change.part == "refs":
            return f"refs {redact(change.path)}"
        if isinstance(change, PlannedChange) and change.is_order:
            return f"order of {redact(change.path)}"
        return redact(change.path)

    return ", ".join(shown(change) for change in changes) or "none"


def stored_otherwise(lost_edits: Iterable[PlannedChange]) -> list[Path]:
    """Where in each lost edit the value stored is not the value sent, at the deepest path."""
    return [
        Path((*change.path.parts, *inner.path.parts))
        for change in lost_edits
        for inner in raw_changes(change.intended, change.stored)
    ]


def outcome(error: ModuleWriteError) -> str:
    """*error* as its class and its paths, redacted.

    Its own message names segments and rules of the lab and shows their
    values, so a test reports this in its place. Where a lost edit was
    stored otherwise than sent is a candidate for ``MODULE_REWRITES``. A
    ``WriteOutcomeUnknownError`` lists no lost edit, so it is reported by its
    detail, with each run of digits (the lab's IDs) shown as ``#``, what the
    Orchestrator answered, and the paths that were sent.
    """
    name = error.module_name or "unnamed"
    if isinstance(error, WriteOutcomeUnknownError):
        sent = describe(error.plan.changes) if error.plan is not None else "unknown"
        detail = re.sub(r"\d+", "#", error.detail)
        return (
            f"WriteOutcomeUnknownError ({detail}; answer {error.answer}) "
            f"on the {name} module: sent {sent}"
        )
    kind = type(error).__name__
    if isinstance(error, ConfigConflictError):
        kind += f" ({error.detail})"
    otherwise = ", ".join(redact(p) for p in stored_otherwise(error.lost_edits))
    return (
        f"{kind} on the {name} module: "
        f"lost edits {where(error.lost_edits)}; "
        f"stored otherwise than sent at {otherwise or 'none'}; "
        f"applied {where(error.applied)}; "
        f"server changes {where(error.server_changes)}"
    )


def _stated_refusal(error: ModuleWriteError) -> str:
    """How the Orchestrator stated a refusal, read from the conflict ``update_configuration_module`` raised.

    ``write_module`` raises its error from that conflict, or while handling it
    when the module could not be read back. The conflict is found by walking
    the chain from *error*: each link's ``__cause__``, else its
    ``__context__``, until a ``ConfigConflictError`` (a read-back that failed
    to parse is raised from a ``ValidationError`` that was itself raised while
    handling the conflict, one link deeper). One with a cause of its own was
    raised from the Orchestrator's precondition error, whatever HTTP status
    carried it; one without is a row count of zero. With no such conflict in
    the chain, the refusal is only ``refused``.
    """
    seen = {id(error)}
    link = error.__cause__ if error.__cause__ is not None else error.__context__
    while link is not None and id(link) not in seen:
        if isinstance(link, ConfigConflictError):
            if link.__cause__ is not None:
                return "refused with the precondition error"
            return "refused with a row count of zero"
        seen.add(id(link))
        link = link.__cause__ if link.__cause__ is not None else link.__context__
    return "refused"


def refusal_form(error: ModuleWriteError) -> str:
    """How the Orchestrator answered the stale write that raised *error*, as a clause.

    A ``WriteOutcomeUnknownError`` says what the Orchestrator answered of the
    write: a counted write is no refusal, a stated refusal is told as above
    with the note that the module could not be read, and a row that could not
    be used is no refusal either. A ``WriteNotAppliedError`` is no refusal:
    the Orchestrator counted the write or answered with a row, and stored it
    otherwise than sent (a refusal it stated always raises a
    ``ConfigConflictError``). A ``ConfigConflictError`` that is ``stated`` is
    a stated refusal; one that is not was inferred from the module row the
    Orchestrator answered with.
    """
    unread = ", and the module as stored could not be read"
    if isinstance(error, WriteOutcomeUnknownError):
        if error.answer == "counted":
            return "not refused: the write was counted" + unread
        if error.answer == "refused":
            return _stated_refusal(error) + unread
        return "answered with a row that could not be taken as this module"
    if isinstance(error, WriteNotAppliedError):
        return "not refused: stored otherwise than sent"
    if isinstance(error, ConfigConflictError) and error.stated:
        return _stated_refusal(error)
    return "refused with the module row as stored"


def record(capsys: pytest.CaptureFixture[str], line: str) -> None:
    """Print *line* on a line of its own, past the capture, so a passing test shows it."""
    with capsys.disabled():
        print(f"\n{line}")


def record_refusal(
    capsys: pytest.CaptureFixture[str], label: str, error: ModuleWriteError
) -> None:
    """Print, past the capture, the form of the refusal and the error it made."""
    record(capsys, f"{label}: {refusal_form(error)} ({type(error).__name__})")


def check_entries(
    label: str,
    what: str,
    changes: Sequence[PlannedChange | ServerChange],
    expected: Sequence[Path],
) -> None:
    got = sorted(str(c.path) for c in changes)
    want = sorted(str(p) for p in expected)
    check(
        got == want,
        f"{label}: {what} lists {where(changes)}; expected "
        f"{', '.join(redact(p) for p in expected) or 'none'}",
    )


def check_as_read(
    label: str, held: ConfigurationModuleBase, read: ConfigurationModuleBase
) -> None:
    """*held* is the module as a read made right after shows it: its version and raw data."""
    check(
        held.version == read.version,
        f"{label}: the module held has another version than a read made right after",
    )
    check(
        held.raw_data == read.raw_data,
        f"{label}: the module held differs from a read made right after: "
        + describe(raw_changes(read.raw_data, held.raw_data, data_model(read))),
    )


def value_at(document: object, path: Path) -> object:
    """The value at *path*, a path of names only, in a JSON document; ``MISSING`` if there is none."""
    value = document
    for part in path.parts:
        if not isinstance(value, dict) or not isinstance(part, str):
            return MISSING
        value = cast(dict[str, object], value).get(part, MISSING)
    return value


def same_value(a: object, b: object) -> bool:
    """Equal, but for the keys the Orchestrator assigns to an item it was given."""

    def bare(value: object) -> object:
        if isinstance(value, dict):
            return {
                k: v
                for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
                if k not in SERVER_ASSIGNED_KEYS
            }
        return value

    return bare(a) == bare(b)


def check_paths(
    label: str, what: str, changes: Sequence[RawChange], expected: Sequence[Path]
) -> None:
    got = sorted(str(c.path) for c in changes)
    want = sorted(str(p) for p in expected)
    check(
        got == want,
        f"{label}: {what} lists {describe(changes)}; expected "
        f"{', '.join(redact(p) for p in expected)}",
    )


# ---------------------------------------------------------------------------
# What a failed test reports
# ---------------------------------------------------------------------------


def failure_text(error: BaseException) -> str | None:
    """*error* as what holds no lab data, or ``None`` where the report is left as it is.

    It keeps the class, error codes and types, redacted paths and the
    ``outcome`` of a write, and nothing else of the error: not its message or
    arguments, not the notes a save adds, not the text of an error it was raised
    from, any of which can quote names and values of the lab.
    """
    kind = type(error).__name__
    if isinstance(error, (pytest.fail.Exception, pytest.skip.Exception)):
        return None
    if isinstance(error, LiveSuiteError):
        return str(error)
    if isinstance(error, ModuleWriteError):
        return outcome(error)
    if isinstance(error, VcoOperationError):
        return f"{kind} in {error.operation}: {refusal_codes(error)}"
    if isinstance(error, VcoHttpError):
        return f"{kind}: HTTP {error.status_code}"
    if isinstance(error, PatchError):
        return f"{kind} ({error.kind}) at {redact(error.path)}"
    if isinstance(error, LossyFieldError):
        paths = ", ".join(redact(path) for path in error.paths)
        return f"{kind} in the {error.module} module at {paths}"
    if isinstance(error, ValidationError):
        types = sorted({str(entry["type"]) for entry in error.errors()})
        return (
            f"{kind} for {error.title}: {error.error_count()} error(s) "
            f"of type {', '.join(types)}"
        )
    entries = getattr(error, "errors", None)
    if isinstance(entries, list):
        listed = cast(list[object], entries)
        codes = sorted(
            {
                code
                for entry in listed
                if isinstance(code := getattr(entry, "code", None), str)
            }
        )
        return (
            f"{kind}: {len(listed)} error(s) with code {', '.join(codes) or 'unknown'}"
        )
    return kind


def redact_report(
    item: pytest.Item, call: pytest.CallInfo[None], report: pytest.TestReport
) -> None:
    """Replace the report of a failed live configuration test by ``failure_text`` and the line that raised.

    A test is a live configuration test if it is marked ``integration`` and
    uses the ``lab`` fixture; the analytics tests assert on values and are left
    alone. The place is the last frame in the tests' own code: a frame of this
    support module, or of a library the test called, is not where the test went
    wrong. Where no frame is in a ``tests`` directory it is the last frame.
    """
    if not report.failed or call.excinfo is None:
        return
    if item.get_closest_marker("integration") is None:
        return
    # Only a test function has fixtures; ``Item`` is the signature the hook has.
    if "lab" not in cast(list[str], getattr(item, "fixturenames", [])):
        return
    text = failure_text(call.excinfo.value)
    if text is None:
        return
    entries = list(call.excinfo.traceback)
    paths = [pathlib.Path(str(entry.path)) for entry in entries]
    in_tests = [i for i, path in enumerate(paths) if "tests" in path.parts]
    own = [i for i in in_tests if "support" not in paths[i].parts]
    at = (own or in_tests or [len(entries) - 1])[-1]
    report.longrepr = f"{text} (raised at {paths[at].name}:{entries[at].lineno + 1})"
