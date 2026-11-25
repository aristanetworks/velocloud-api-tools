"""Support for the live configuration-save tests: reading, restoring and redacting.

The tests that use this write to a real Orchestrator, so everything here is
about leaving it as it was found. Nothing names a lab entity: targets are found
at run time, and every message refers to them by position and module type only.

The live tests import this module by name: its directory is on ``pythonpath``
in the root ``pyproject.toml``. It is test support and no part of the library.

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

The tests that make a hub of an Edge or a cluster need one more:

    PYVELO_IT_HUB                   the logical ID of the Edge or the hub cluster
                                    the suite may make a hub of a marked profile
                                    and remove again; unset, those tests skip

    uv run --env-file .env pytest -m "integration and not live_write"   # reads only
    uv run --env-file .env pytest -m integration                        # everything

Within that enterprise the suite writes only to what its owner marked, with
a word in the description of the edge or profile on the Orchestrator:

    pyvelo-it-target            the suite may edit the modules of this edge
                                or profile; it puts each back as it found it
    pyvelo-it-with-edges        on a profile, beside pyvelo-it-target: it may
                                be edited though edges are assigned to it
                                (each of them receives every change)
    pyvelo-it-replace-modules   on an edge, beside pyvelo-it-target: the suite
                                may also delete and insert whole modules of
                                the edge (its firewall and QoS)

A marked profile with edges assigned and no ``pyvelo-it-with-edges`` is
passed over. Everything unmarked is read and never written. With nothing
marked, each test that writes is skipped and says which mark it wants.

A test that writes is marked ``live_write``. The client the suite connects
(``connect``) refuses, with ``WritesRefused`` and before anything is sent:

* every portal call that is not a read, except the three writes the suite
  makes itself: ``update_configuration_module`` and
  ``insert_configuration_module`` of the client, and ``delete_module``, and
  the twelve role calls of a hub (``enable_edge_hub``, ``disable_cluster_backhaul``
  and the rest, below). A read is a method whose name starts with ``get``,
  ``list`` or ``is``. This covers a raw ``client.portal(...)`` and every other
  write method of the client (``create_edge``, ``delete_edge``, the enterprise,
  object-group and network-segment writes, the diagnostic bundles and the
  rest);
* those three writes from a test that is not marked ``live_write``, outside a
  ``restoring`` block, or to a module no open block found or inserted: the
  suite can only write what it will put back;
* a role call of a hub unless the test is marked ``live_write``, a
  ``restoring`` block is open on the device settings module of a marked
  profile that the call's ``configurationId`` and ``moduleId`` name, and the
  call's ``id`` is the Edge or the cluster ``admit_hub`` found for
  ``PYVELO_IT_HUB`` (an Edge's role call takes an Edge ID, a cluster's a
  cluster ID: one for the wrong kind is refused).

The restore of a module does not undo a role call: a cluster keeps its role
data and a deleted Edge hub service is not made again. A test that makes a hub
removes it again itself, in a ``finally``.

One hole is left: a second client that a test builds itself is not guarded
(nor are the websocket calls of the client, remote diagnostics). A live test
uses the ``vco_client`` fixture.

``restoring`` is the one way a test writes: it records a module's raw data
first (in a private file under ``$PYVELO_IT_BACKUP_DIR``, which must be outside
the repository, else a new temp directory; it holds no refs but a QoS module's,
kept to insert it again, and never a client-connector ref; it is removed once
the module is back) and,
however the test ends, puts it back and checks that it is back. The restore
sends the data, and, for a deviceSettings or QoS module whose data has
segments, the module's refs as read in the same step (never from the backup),
less the append-only names, as every pipeline save of such a module does: the
VCO runs its processors on every such write, and a write without refs could
make them replace the module's whole association set. Any other module is
restored with data alone. The restore
undoes what this run did and nothing else. A module this run inserted is
deleted; one it deleted is inserted again; one it wrote gets its original data
back. Where the module is not as the Orchestrator last answered this run
(another writer changed it, added it or removed it), the restore leaves it
alone and fails the test. On an Orchestrator that answers a write with a row
count, what it stored is not known, and the original is written back without
that comparison. Ctrl-C is held while a module is put back. A failure names the
backup file and never prints the data, and every backup still there when the
run ends is listed in its summary.

What the Orchestrator does to a module as it stores it, seen on a lab:

* It adds ``routeMapsVersion`` to a deviceSettings segment entry that lacks it
  on a write (``settle``). A comparison ignores that key where the module as it
  was before the write lacked it; the key is then accepted drift, and it stays
  in a module the suite has written.
* It re-derives the refs of a deviceSettings module from its data when the data
  changes: ref IDs are re-issued, and a ref whose object is gone is dropped (it
  cannot be written back). The refs of a firewall or WAN module are left as they
  were. A client-connector ref carries a login token that is new on every read.
  A save through the library of a deviceSettings or QoS module with segments
  sends its complete refs as read (the derived refs), so a test that saves
  one leaves the refs to the library, and a test edits no ref but through the
  field that owns it. The restore sends the refs as read at the restore, as
  described above, and compares data only.
* It assigns ``ruleLogicalId`` to a rule added without one (the tests that add
  rules allow it).
* Some modules of a lab are not valid as they stand and refuse any write, even
  of their own data: ``pick_writable_edge`` passes over those, and fails the
  test when every marked candidate refuses.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import pathlib
import re
import signal
import sys
import tempfile
import threading
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
from contextlib import aclosing, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, NoReturn, cast

import httpx
import pytest
from pydantic import MISSING, BaseModel, RootModel, ValidationError
from pyvelo.client import (
    ConfigConflictError,
    VcoClient,
    VcoEmptyResultError,
    VcoHttpError,
    VcoOperationError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.derived_refs import APPEND_ONLY_REF_NAMES, sends_refs_always
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import (
    GetConfigurationWith,
    GetEnterpriseConfigurationsWith,
)
from pyvelo.client.exceptions import (
    LossyFieldError,
    ModuleWriteError,
    is_precondition_answer,
)
from pyvelo.client.module_check import data_model
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
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
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.core import (
    Alias,
    ConfigContext,
    NoneIsMissing,
    PaginatedVcoModel,
    PatchError,
    RowsModifiedConfirmation,
    VcoModel,
)
from pyvelo.core.identity import HubLogicalId, ResolutionError
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes
from pyvelo.core.response_check import PlannedChange, ServerChange

SYNTHETIC_PREFIX = "pyvelo-it-"
"""Every rule, description and the like these tests add starts with this."""

TARGET_MARK = "pyvelo-it-target"
WITH_EDGES_MARK = "pyvelo-it-with-edges"
REPLACE_MODULES_MARK = "pyvelo-it-replace-modules"
"""The words an owner puts in the description of an edge or profile (see the docstring)."""

_MARKS = (TARGET_MARK, WITH_EDGES_MARK, REPLACE_MODULES_MARK)

REPLACE_MODULES_REMEDY = (
    f"put {REPLACE_MODULES_MARK} beside {TARGET_MARK} in the description of an "
    "edge the suite may delete and insert modules of"
)
NOT_A_TARGET = (
    f"the test tried to write to an edge or profile that is not marked {TARGET_MARK}; "
    "nothing was written; take the target from pick_writable_edge or "
    "pick_writable_profile, which pass over what is not marked, or put "
    f"{TARGET_MARK} in the description of an edge or profile the suite may write to"
)
NO_EDGE_MARKED = (
    f"no edge is marked for this suite: put {TARGET_MARK} in the description of an "
    "edge it may write to"
)
NO_EDGE_FOR_MODULES = (
    f"no edge is marked {REPLACE_MODULES_MARK} as well as {TARGET_MARK}: this test "
    f"deletes and inserts a module of the edge; {REPLACE_MODULES_REMEDY}"
)
NO_PROFILE_MARKED = (
    f"no profile is marked for this suite: put {TARGET_MARK} in the description of "
    f"a profile it may write to, and {WITH_EDGES_MARK} as well if edges are "
    "assigned to it"
)


def synthetic_name() -> str:
    return f"{SYNTHETIC_PREFIX}{uuid.uuid4().hex[:10]}"


def marks_in(description: object) -> frozenset[str]:
    """The marks that are whole tokens of *description*; a mark inside a longer token is none.

    A token is a run of letters, digits, underscores, hyphens and slashes, so
    ``pyvelo-it-target_old`` and ``pyvelo-it-target/prod`` hold no mark. Anything
    that is not a string has no marks.
    """
    if not isinstance(description, str):
        return frozenset()
    tokens = set(re.findall(r"[\w/-]+", description))
    return frozenset(mark for mark in _MARKS if mark in tokens)


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


def check(condition: bool, message: str) -> None:  # noqa: FBT001 (an assert as a function: the condition is what it checks, not a flag)
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
    marks: frozenset[str] = frozenset()

    @property
    def target(self) -> bool:
        """Whether the owner marked the edge as one the suite may write to."""
        return TARGET_MARK in self.marks

    @property
    def replace_modules(self) -> bool:
        """Whether the suite may also delete and insert modules of the edge."""
        return self.target and REPLACE_MODULES_MARK in self.marks

    def has(self, name: ConfigurationModuleName) -> bool:
        return find_module(self.edge, name) is not None


@dataclass(frozen=True)
class ProfileEntry:
    """One profile of the lab: its ID, its marks and how many edges are assigned to it."""

    profile_id: int
    marks: frozenset[str] = frozenset()
    edge_count: int | None = None
    """``None`` where the Orchestrator did not say: counted as edges assigned."""

    @property
    def target(self) -> bool:
        """Marked, and either with no edge assigned or marked to be written with edges."""
        return TARGET_MARK in self.marks and (
            self.edge_count == 0 or WITH_EDGES_MARK in self.marks
        )


def find_module(
    config: EdgeConfiguration | EnterpriseConfigurationProfile,
    name: ConfigurationModuleName | str,
) -> AnyStoredModule | None:
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
) -> EdgeConfiguration | EnterpriseConfigurationProfile:
    request = GetConfiguration(id_=profile_id, with_=[GetConfigurationWith.MODULES])
    if enterprise_id is not None:
        request.enterprise_id = enterprise_id
    return await client.get_configuration(request)


LAB_LIMIT = 50
"""At most this many edges, and this many profiles, are read and swept."""


async def list_edges(
    client: VcoClient, enterprise_id: int | None
) -> list[tuple[int, frozenset[str]]]:
    """The ID of each edge, with the marks in its description."""
    edges = await client.get_enterprise_edges(
        GetEnterpriseEdges(enterprise_id=enterprise_id or MISSING)
    )
    return [(e.id_, marks_in(e.description)) for e in edges if isinstance(e.id_, int)][
        :LAB_LIMIT
    ]


async def list_profiles(
    client: VcoClient, enterprise_id: int | None
) -> list[ProfileEntry]:
    """Each profile, with the marks in its description and the number of edges assigned."""
    profiles = await client.get_enterprise_configurations(
        GetEnterpriseConfigurations(
            enterprise_id=enterprise_id or MISSING,
            with_=[GetEnterpriseConfigurationsWith.EDGE_COUNT],
        )
    )
    return [
        ProfileEntry(
            p.id_,
            marks_in(p.description),
            p.edge_count if isinstance(p.edge_count, int) else None,
        )
        for p in profiles
        if isinstance(p.id_, int)
    ][:LAB_LIMIT]


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
    for index, (edge_id, marks) in enumerate(await list_edges(client, enterprise_id)):
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
                marks=marks,
            )
        )
    return layouts


@dataclass(frozen=True)
class Lab:
    """What the Orchestrator has, as found by one read of every edge and profile."""

    client: VcoClient
    enterprise_id: int | None
    layouts: list[EdgeLayout]
    profiles: list[ProfileEntry]

    @property
    def profile_ids(self) -> list[int]:
        """Every profile, marked or not: the read-only sweeps read them all."""
        return [entry.profile_id for entry in self.profiles]

    @property
    def targets(self) -> list[EdgeLayout]:
        """The edges the owner marked as ones the suite may write to."""
        return [layout for layout in self.layouts if layout.target]

    def edge_site(self, layout: EdgeLayout, name: ConfigurationModuleName) -> Site:
        return edge_site(self.client, layout, name, self.enterprise_id)

    def profile_site(self, profile_id: int, name: ConfigurationModuleName) -> Site:
        target = any(
            entry.target for entry in self.profiles if entry.profile_id == profile_id
        )
        return profile_site(
            self.client, profile_id, name, self.enterprise_id, target=target
        )

    def require_edge_targets(
        self, *, replace_modules: bool = False
    ) -> list[EdgeLayout]:
        """The marked edges, once the test may write; else fail or skip the test, saying which mark is missing.

        The opt-in is checked first, so a run that may not write fails or skips
        for that and never for an unmarked lab. With *replace_modules*, some
        marked edge must also be marked for deleting and inserting modules.
        """
        require_writes(run_of(self.client))
        targets = self.targets
        if not targets:
            pytest.skip(NO_EDGE_MARKED)
        if replace_modules and not any(layout.replace_modules for layout in targets):
            pytest.skip(NO_EDGE_FOR_MODULES)
        return targets

    def edges_with(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyStoredModule], bool] = lambda _: True,
        *,
        replace_modules: bool = False,
    ) -> list[EdgeLayout]:
        """The marked edges whose own configuration has a *name* module that is *suitable*.

        With *replace_modules*, only those marked for deleting and inserting modules.
        """
        found: list[EdgeLayout] = []
        for layout in self.targets:
            if replace_modules and not layout.replace_modules:
                continue
            module = find_module(layout.edge, name)
            if module is not None and suitable(module):
                found.append(layout)
        return found

    def edges_lacking(self, name: ConfigurationModuleName) -> list[EdgeLayout]:
        """The edges marked for deleting and inserting modules whose own configuration has no *name* module."""
        return [
            layout
            for layout in self.targets
            if layout.replace_modules and find_module(layout.edge, name) is None
        ]

    async def _first_that_takes_a_write(
        self, layouts: Sequence[EdgeLayout], name: ConfigurationModuleName
    ) -> tuple[EdgeLayout | None, list[str]]:
        """The first marked one of *layouts* whose *name* module takes a write, and the refusal of each one tried before it."""
        refusals: list[str] = []
        for layout in layouts:
            if not layout.target:
                continue
            refusal = await own_data_refusal(self.edge_site(layout, name))
            if refusal is None:
                return layout, refusals
            refusals.append(refusal)
        return None, refusals

    async def first_writable(
        self, layouts: Sequence[EdgeLayout], name: ConfigurationModuleName
    ) -> EdgeLayout | None:
        """The first of *layouts* that is marked and whose *name* module takes a write.

        ``None`` when none does. An error of the write other than the
        Orchestrator's validation error is raised.
        """
        require_writes(run_of(self.client))
        layout, _ = await self._first_that_takes_a_write(layouts, name)
        return layout

    async def first_writable_or_fail(
        self, layouts: Sequence[EdgeLayout], name: ConfigurationModuleName
    ) -> EdgeLayout | None:
        """``first_writable``, but the test fails where *layouts* has marked edges and every one refuses.

        ``None`` only when none of *layouts* is marked: there was nothing to try.
        """
        require_writes(run_of(self.client))
        layout, refusals = await self._first_that_takes_a_write(layouts, name)
        if layout is None and refusals:
            _none_takes_a_write("edge", name, refusals)
        return layout

    async def pick_writable_edge(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyStoredModule], bool] = lambda _: True,
        what: str = "",
        *,
        replace_modules: bool = False,
    ) -> EdgeLayout:
        """The first marked edge whose module takes a write; skip the test where none is a candidate, fail it where all refuse.

        With *replace_modules*, the edge must also be marked for deleting and
        inserting modules.
        """
        self.require_edge_targets(replace_modules=replace_modules)
        found = self.edges_with(name, suitable, replace_modules=replace_modules)
        if not found:
            pytest.skip(f"no marked edge has a {name.value} module {what or 'to use'}")
        layout = await self.first_writable_or_fail(found, name)
        assert layout is not None  # every edge of *found* is marked
        return layout

    async def pick_writable_profile(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyStoredModule], bool] = lambda _: True,
        what: str = "",
    ) -> int:
        """The ID of the first marked profile with such a module that takes a write; skip the test where none is a candidate, fail it where all refuse."""
        require_writes(run_of(self.client))
        targets = [entry.profile_id for entry in self.profiles if entry.target]
        if not targets:
            pytest.skip(NO_PROFILE_MARKED)
        refusals: list[str] = []
        for profile_id in targets:
            config = await read_profile(self.client, profile_id, self.enterprise_id)
            module = find_module(config, name)
            if module is None or not suitable(module):
                continue
            refusal = await own_data_refusal(self.profile_site(profile_id, name))
            if refusal is None:
                return profile_id
            refusals.append(refusal)
        if refusals:
            _none_takes_a_write("profile", name, refusals)
        pytest.skip(f"no marked profile has a {name.value} module {what or 'to use'}")


def _none_takes_a_write(
    kind: str, name: ConfigurationModuleName, refusals: Sequence[str]
) -> NoReturn:
    """Fail the test: each marked *kind* it could write to refused a write of its own data."""
    pytest.fail(
        f"none of the {len(refusals)} marked {kind}(s) with a suitable {name.value} "
        f"module takes a write of its own data ({'; '.join(refusals)}): the modules "
        "are not valid as they stand, or the update path is broken",
        pytrace=False,
    )


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
    entries = cast("dict[str, object]", data).get("error")
    if not isinstance(entries, list):
        return []
    found: list[tuple[str, str]] = []
    for entry in cast("list[object]", entries):
        if not isinstance(entry, dict):
            continue
        fields = cast("dict[str, object]", entry)
        code, path = fields.get("code"), fields.get("path")
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
            found.append((code, _shown_field(path) if isinstance(path, str) else ""))
    return found


class DataRefused(VcoOperationError):
    """A write the Orchestrator refused with a validation error: its message is the codes alone."""


def _states_errors(exc: VcoOperationError) -> bool:
    """Whether the data of *exc* has a non-empty ``error`` list: the Orchestrator's validation error."""
    data: object = exc.data
    if not isinstance(data, dict):
        return False
    entries = cast("dict[str, object]", data).get("error")
    return isinstance(entries, list) and bool(entries)


def _stored_nothing(exc: BaseException, *, unsent: bool) -> bool:
    """Whether a write that raised *exc* is known to have stored nothing.

    So it is where nothing was sent, where the version was refused, and where
    the Orchestrator refused the data with errors it states. Any other failure,
    a cancellation or an interrupt included, may have come after it stored.
    """
    if unsent or isinstance(exc, ConfigConflictError):
        return True
    return (
        isinstance(exc, VcoOperationError)
        and not isinstance(exc, WriteOutcomeUnknownError)
        and _states_errors(exc)
    )


def _redacted(exc: VcoOperationError) -> VcoOperationError:
    """*exc* with each error code and field kept and no message; ``refusal_codes`` reads it as it reads *exc*.

    A refusal that states its errors in its data is a ``DataRefused``; any other
    error stays a plain ``VcoOperationError``.
    """
    errors = [
        {"code": code, "path": field, "message": ""}
        for code, field in _found_codes(exc)
    ]
    if _states_errors(exc):
        return DataRefused(
            exc.operation, refusal_codes(exc), code=exc.code, data={"error": errors}
        )
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
HUB_ENV = "PYVELO_IT_HUB"

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


NO_HUB = (
    f"no hub is named for this suite: set {HUB_ENV} to the logical ID of an Edge "
    "or a hub cluster of the enterprise that this suite may make a hub of a "
    "marked profile, and remove again"
)
HUB_NOT_A_LOGICAL_ID = (
    f"{HUB_ENV} is not a logical ID; nothing was written; set it to the logical "
    "ID of the Edge or the hub cluster, in the form 8-4-4-4-12 hexadecimal digits"
)
HUB_NOT_FOUND = (
    f"{HUB_ENV} names no Edge and no hub cluster of the enterprise this run "
    "addresses (an Edge that is a member of a cluster is no hub of its own: name "
    "the cluster); nothing was written; set it to the logical ID of one that exists"
)
ROLE_CALL_NOT_ADMITTED = (
    "a role call of a hub, which is not a write the suite may send here; nothing "
    f"was sent; call admit_hub first, which reads {HUB_ENV}, inside the test"
)
ROLE_CALL_NOT_COVERED = (
    "a role call of a hub for a profile and module that no open restoring block "
    "covers, or whose profile is not marked "
    f"{TARGET_MARK}; nothing was sent; open `restoring(site)` on the device "
    "settings module of a marked profile and send the call inside it"
)
ROLE_CALL_NOT_THE_HUB = (
    f"a role call of a hub for an Edge or a cluster that {HUB_ENV} does not "
    "name; nothing was sent; the suite makes only that hub, and an Edge's role "
    "call takes an Edge ID, a cluster's a cluster ID"
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
        granted=False,
        refusal=f"writes refused: {reason}; nothing was written; {remedy}",
        misconfigured=True,
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
    except VcoHttpError as exc:
        return _refused(
            "the enterprise this run addresses could not be read from the "
            f"Orchestrator (HTTP {exc.status_code})",
            "check that VCO_HOST names an Orchestrator that answers and that "
            "VCO_TOKEN is accepted by it, then run again",
        )
    except VcoEmptyResultError:
        return _refused(
            "the enterprise this run addresses could not be read from the "
            "Orchestrator (no answer)",
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
def writer_test(*, marked: bool) -> Generator[None]:
    """For the length of the block, the test running is a writer (*marked*) or is not."""
    global _marked
    before = _marked
    _marked = marked
    try:
        yield
    finally:
        _marked = before


_UNKNOWN: object = object()
"""In ``LiveRun.seen``: the Orchestrator answered a write without the module row, or the answer is lost."""


type Block = tuple[Site, int | None]
"""An open ``restoring`` block: its site, and the ID of the module it found there."""


@dataclass(frozen=True)
class HubTarget:
    """The hub ``PYVELO_IT_HUB`` names: its kind and the ID a role call takes."""

    kind: Literal["edge", "cluster"]
    id_: int
    """The Edge ID or the cluster ID: the ``id`` of the hub's role calls."""
    logical_id: uuid.UUID
    """The logical ID the variable holds: how a patch names the hub."""


@dataclass
class LiveRun:
    """What one client may write, and what it has open to write: the guard's state."""

    access: WriteAccess = field(default_factory=WriteAccess)
    hub: HubTarget | None = None
    """The hub the role calls may be sent for, set by ``admit_hub``."""
    open: list[Block] = field(default_factory=list[Block])
    """The open ``restoring`` blocks, the innermost last."""
    inserted: dict[int, Block] = field(default_factory=dict[int, Block])
    """The ID of each module a block's test inserted, with the block that was open for it."""
    deleted: set[int] = field(default_factory=set[int])
    """The ID of each module this run deleted."""
    sent: int = 0
    """How many portal calls that are not reads the client has begun: a write that raised with this unchanged sent nothing."""
    seen: dict[int, object] = field(default_factory=dict[int, object])
    """Each module's raw data as the Orchestrator last answered this run's write with, or ``_UNKNOWN``."""

    def _check_may_write(self) -> None:
        if not _marked:
            raise WritesRefused(NOT_MARKED)
        if not self.access.granted:
            raise WritesRefused(self.access.refusal)
        if not self.open:
            raise WritesRefused(OUTSIDE_RESTORING)

    def _covers(self, module_id: int) -> bool:
        """Whether an open block found module *module_id* or inserted it."""
        found = {module for _, module in self.open if module is not None}
        return module_id in found or module_id in self.inserted

    def block_for(self, configuration_id: int, name: str) -> Block | None:
        """The innermost open block on that configuration's *name* module."""
        return next(
            (
                block
                for block in reversed(self.open)
                if block[0].configuration_id == configuration_id
                and block[0].name.value == name
            ),
            None,
        )

    def close(self, block: Block) -> None:
        """Take *block* out of the open ones, and with it the modules it inserted."""
        self.open[:] = [b for b in self.open if b is not block]
        for module_id in [m for m, b in self.inserted.items() if b is block]:
            del self.inserted[module_id]

    def check_update(self, module_id: int) -> None:
        """Raise ``WritesRefused`` unless the update of *module_id* is one a block will put back."""
        self._check_may_write()
        if not self._covers(module_id):
            raise WritesRefused(f"an update of a module {NOT_COVERED}")

    def check_replace(self, configuration_id: int, name: str, what: str) -> None:
        """Raise ``WritesRefused`` unless an open block is on that configuration's *name* module.

        *what* is ``"an insert"`` or ``"a delete"``. The block's site must also
        be one marked for deleting and inserting modules.
        """
        self._check_may_write()
        blocks = [
            block
            for block in self.open
            if block[0].configuration_id == configuration_id
            and block[0].name.value == name
        ]
        if not blocks:
            raise WritesRefused(f"{what} of a {name} module {NOT_COVERED}")
        if not any(site.replace_modules for site, _ in blocks):
            raise WritesRefused(
                f"{what} of a {name} module of an edge that is not marked "
                f"{REPLACE_MODULES_MARK}; nothing was sent; {REPLACE_MODULES_REMEDY}"
            )

    def check_role_call(
        self,
        kind: Literal["edge", "cluster"],
        configuration_id: int,
        module_id: int,
        target_id: int,
    ) -> None:
        """Raise ``WritesRefused`` unless the role call is for the named hub on a marked profile a block covers.

        *kind* is the kind of hub the call is for, *target_id* its ``id``, and
        *configuration_id* and *module_id* the profile and its device settings
        module. A block must be open on that module of a profile marked as a
        target (``Site.profile``), found by ID.
        """
        self._check_may_write()
        if self.hub is None:
            raise WritesRefused(ROLE_CALL_NOT_ADMITTED)
        if not any(
            site.profile
            and site.target
            and site.name == ConfigurationModuleName.DEVICE_SETTINGS
            and site.configuration_id == configuration_id
            and found == module_id
            for site, found in self.open
        ):
            raise WritesRefused(ROLE_CALL_NOT_COVERED)
        if (kind, target_id) != (self.hub.kind, self.hub.id_):
            raise WritesRefused(ROLE_CALL_NOT_THE_HUB)

    def check_delete(self, configuration_id: int, name: str, module_id: int) -> None:
        """Raise ``WritesRefused`` unless a block is on that module and *module_id* is one it found or inserted."""
        self.check_replace(configuration_id, name, "a delete")
        if not self._covers(module_id):
            raise WritesRefused(f"a delete of a {name} module {NOT_COVERED}")


def require_writes(run: LiveRun) -> None:
    """Fail where the test may not write, skip where this run may not, else return."""
    if not _marked:
        pytest.fail(NOT_MARKED, pytrace=False)
    if run.access.misconfigured:
        pytest.fail(run.access.refusal, pytrace=False)
    if not run.access.granted:
        pytest.skip(run.access.refusal)


_READ = re.compile(r"(get|list|is)[A-Z]")

_sending: ContextVar[str | None] = ContextVar("live_support_sending", default=None)
"""The one write method the code now running has checked and may send."""


@contextmanager
def _sends(method: str) -> Generator[None]:
    """For the length of the block, the client may send *method*: the caller has checked the write."""
    token = _sending.set(method)
    try:
        yield
    finally:
        _sending.reset(token)


def check_call(method: str) -> None:
    """Raise ``WritesRefused`` for a portal call that is neither a read nor a write already checked.

    A read is a method whose name, after its namespace, starts with ``get``,
    ``list`` or ``is``. A write passes only from the code that checked it:
    ``update_configuration_module`` and ``insert_configuration_module`` as
    ``run_of`` guards them, ``delete_module``, and the twelve role calls of a
    hub as ``run_of`` guards them (``ROLE_CALLS``).
    """
    if _READ.match(method.rsplit("/", 1)[-1]) or _sending.get() == method:
        return
    raise WritesRefused(
        f"a call to {method}, which is not a read and is not a write the suite "
        "checks; nothing was sent; the live suite writes only module updates and "
        "inserts through the client's own methods, deletes through "
        "delete_module, and the role calls of the hub it admitted through the "
        "client's own methods, each inside a restoring block, so leave this call "
        "out of a live test"
    )


_RUNS: weakref.WeakKeyDictionary[VcoClient, LiveRun] = weakref.WeakKeyDictionary()


def _checked_call(
    original: Callable[..., Awaitable[Any]], run: LiveRun
) -> Callable[..., Awaitable[Any]]:
    """*original*, a portal method that takes the method string first, with ``check_call`` before it.

    A write that passes is counted in ``run.sent``.
    """

    async def checked(method: str, *args: Any, **kwargs: Any) -> Any:
        check_call(method)
        if not _READ.match(method.rsplit("/", 1)[-1]):
            run.sent += 1
        return await original(method, *args, **kwargs)

    return checked


ROLE_CALLS: dict[str, tuple[str, Literal["edge", "cluster"]]] = {
    "enable_edge_hub": ("edge/enableEdgeForEdgeHub", "edge"),
    "enable_edge_to_edge_bridge": ("edge/enableEdgeForEdgeToEdgeBridge", "edge"),
    "enable_edge_backhaul": ("edge/enableEdgeForBackHaul", "edge"),
    "disable_edge_hub": ("edge/disableEdgeForEdgeHub", "edge"),
    "disable_edge_to_edge_bridge": ("edge/disableEdgeForEdgeToEdgeBridge", "edge"),
    "disable_edge_backhaul": ("edge/disableEdgeForBackHaul", "edge"),
    "enable_cluster_hub": ("enterprise/enableClusterForEdgeHub", "cluster"),
    "enable_cluster_to_edge_bridge": (
        "enterprise/enableClusterForEdgeToEdgeBridge",
        "cluster",
    ),
    "enable_cluster_backhaul": ("enterprise/enableClusterForBackHaul", "cluster"),
    "disable_cluster_hub": ("enterprise/disableClusterForEdgeHub", "cluster"),
    "disable_cluster_to_edge_bridge": (
        "enterprise/disableClusterForEdgeToEdgeBridge",
        "cluster",
    ),
    "disable_cluster_backhaul": ("enterprise/disableClusterForBackHaul", "cluster"),
}
"""The twelve role calls of a hub: each client method, with its portal method and the kind of hub it is for."""


def _guarded_role_call(
    run: LiveRun,
    original: Callable[..., Awaitable[Any]],
    method: str,
    kind: Literal["edge", "cluster"],
) -> Callable[..., Awaitable[Any]]:
    """*original*, a role call of the client, checked by ``LiveRun.check_role_call`` before it is sent.

    A call that passes may send *method* (``_sends``), and the portal counts
    the send in ``run.sent``. A refusal of the Orchestrator carries its codes
    alone.
    """

    async def guarded(request: Any) -> Any:
        run.check_role_call(
            kind, request.configuration_id, request.module_id, request.id_
        )
        try:
            with _sends(method):
                return await original(request)
        except VcoOperationError as exc:
            raise _redacted(exc) from None

    return guarded


async def admit_hub(
    client: VcoClient,
    enterprise_id: int | None,
    environ: Mapping[str, str] | None = None,
) -> HubTarget:
    """Admit the role calls of the hub ``PYVELO_IT_HUB`` names, and return it.

    The test may write (``require_writes``) before anything else is checked;
    the variable unset skips the test and says what to set. The logical ID is
    looked up among the Edges and clusters of the enterprise the run
    addresses, as ``HubResolver`` finds a hub, and the run's guard then admits
    the role calls for that Edge or cluster alone (``LiveRun.check_role_call``).
    No message holds the ID.
    """
    run = run_of(client)
    require_writes(run)
    variables = os.environ if environ is None else environ
    named = variables.get(HUB_ENV, "").strip()
    if not named:
        pytest.skip(NO_HUB)
    try:
        logical_id = uuid.UUID(named)
    except ValueError:
        pytest.fail(HUB_NOT_A_LOGICAL_ID, pytrace=False)
    resolver = HubResolver(client)
    await resolver.load(ResolverContext(enterprise_id=enterprise_id))
    try:
        identity = await resolver.resolve_key(HubLogicalId(logical_id))
    except ResolutionError:
        pytest.fail(HUB_NOT_FOUND, pytrace=False)
    target_id = identity.edge_id if identity.kind == "edge" else identity.cluster_id
    check(target_id is not None, "the hub the variable names has no ID")
    assert target_id is not None
    run.hub = HubTarget(identity.kind, target_id, logical_id)
    return run.hub


def run_of(client: VcoClient) -> LiveRun:
    """The run of *client*, made on the first call, which also puts the guard on the client.

    The guard is on the client's own methods, so a test that forgets every
    helper is refused too. It covers every portal call the client makes
    (``portal``, ``portal_list``, ``portal_paginated`` and
    ``portal_paginated_list``): a read passes, and a write passes only from
    ``update_configuration_module`` or ``insert_configuration_module`` (inside
    a ``restoring`` block, to a module that block covers), from
    ``delete_module``, or from a role call of the hub ``admit_hub`` admitted
    (inside a ``restoring`` block on the device settings module of a marked
    profile). A refused write carries its codes alone: the
    Orchestrator's validation text quotes the names of what it refuses, and a
    refused write in a test would put them in the test output.

    It does not cover a second client a test builds itself, nor the websocket
    calls of a client (remote diagnostics).
    """
    run = _RUNS.get(client)
    if run is not None:
        return run
    run = LiveRun()
    _RUNS[client] = run
    update = client.update_configuration_module
    insert = client.insert_configuration_module
    paginated = client.portal_paginated

    async def guarded_update(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyStoredModule | None:
        module_id = request.configuration_module_id
        run.check_update(module_id)
        had, prior, sent = module_id in run.seen, run.seen.get(module_id), run.sent
        # From the send on, what the Orchestrator holds is not known: the write
        # may be stored whatever happens to the answer, an interrupt included.
        run.seen[module_id] = _UNKNOWN
        try:
            with _sends("configuration/updateConfigurationModule"):
                stored = await update(request, level=level)
        except BaseException as exc:
            if _stored_nothing(exc, unsent=run.sent == sent):
                if had:
                    run.seen[module_id] = prior
                else:
                    del run.seen[module_id]
            if isinstance(exc, VcoOperationError):
                raise _redacted(exc) from None
            raise
        run.seen[module_id] = (
            _UNKNOWN if stored is None else copy.deepcopy(stored.raw_data)
        )
        return stored

    async def guarded_insert(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> InsertConfigurationModuleResult | AnyStoredModule:
        run.check_replace(request.configuration_id, request.name, "an insert")
        try:
            with _sends("configuration/insertConfigurationModule"):
                inserted = await insert(request, level=level)
        except VcoOperationError as exc:
            raise _redacted(exc) from None
        block = run.block_for(request.configuration_id, request.name)
        if block is not None:
            run.inserted[inserted.id_] = block
        run.seen[inserted.id_] = (
            _UNKNOWN
            if isinstance(inserted, InsertConfigurationModuleResult)
            else copy.deepcopy(inserted.raw_data)
        )
        return inserted

    async def guarded_paginated[T: BaseModel](
        method: str, request: PaginatedVcoModel, ItemType: type[T]
    ) -> AsyncGenerator[list[T]]:
        check_call(method)
        async with aclosing(paginated(method, request, ItemType)) as pages:
            async for page in pages:
                yield page

    # Instance attributes, which shadow the methods: a spy a test puts over them
    # (``monkeypatch.setattr``) wraps these.
    attributes = vars(client)
    attributes["update_configuration_module"] = guarded_update
    attributes["insert_configuration_module"] = guarded_insert
    attributes["portal_paginated"] = guarded_paginated
    for name, (method, kind) in ROLE_CALLS.items():
        attributes[name] = _guarded_role_call(run, getattr(client, name), method, kind)
    for name in ("portal", "portal_list", "portal_paginated_list"):
        attributes[name] = _checked_call(getattr(client, name), run)
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
        profiles=await list_profiles(client, enterprise_id),
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
    layer: Callable[[], Awaitable[Sequence[AnyStoredModule]]]
    target: bool = False
    """Whether the owner marked the edge or profile as one the suite may write to."""
    replace_modules: bool = False
    """Whether the suite may also delete and insert modules of the edge."""
    profile: bool = False
    """Whether the module is a profile's: only a profile's device settings takes a role call."""

    async def read(self) -> AnyStoredModule | None:
        modules = await self.layer()
        return next((m for m in modules if m.name == self.name), None)

    async def read_present(self) -> AnyStoredModule:
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
    async def layer() -> Sequence[AnyStoredModule]:
        edge, _ = await read_stack(client, layout.edge_id, enterprise_id)
        return [] if edge.modules is MISSING else edge.modules

    return Site(
        client,
        enterprise_id,
        layout.configuration_id,
        name,
        layer,
        target=layout.target,
        replace_modules=layout.replace_modules,
    )


def profile_site(
    client: VcoClient,
    profile_id: int,
    name: ConfigurationModuleName,
    enterprise_id: int | None,
    *,
    target: bool = False,
) -> Site:
    async def layer() -> Sequence[AnyStoredModule]:
        profile = await read_profile(client, profile_id, enterprise_id)
        return [] if profile.modules is MISSING else profile.modules

    return Site(
        client, enterprise_id, profile_id, name, layer, target=target, profile=True
    )


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
    run.check_delete(site.configuration_id, site.name.value, module_id)
    request = DeleteConfigurationModule(id_=module_id)
    if site.enterprise_id is not None:
        request.enterprise_id = site.enterprise_id
    try:
        with _sends("configuration/deleteConfigurationModule"):
            result = await site.client.portal(
                "configuration/deleteConfigurationModule",
                request,
                RowsModifiedConfirmation,
            )
    except VcoOperationError as exc:
        raise _redacted(exc) from None
    check(not result.error, f"deleting the {site.name.value} module was refused")
    run.deleted.add(module_id)
    _ = run.inserted.pop(module_id, None)
    _ = run.seen.pop(module_id, None)


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
                refs=copy.deepcopy(refs) if isinstance(refs, dict) else MISSING,  # pyright: ignore[reportUnknownArgumentType]
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
        descriptor = cast("dict[str, object]", entry).get("segment")
        if isinstance(descriptor, dict):
            return cast("dict[str, object]", descriptor).get("segmentId")
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
    settled = copy.deepcopy(cast("dict[str, Any]", after))
    had = {
        _segment_key(entry): SEGMENT_DRIFT_KEY in cast("dict[str, object]", entry)
        for entry in cast("dict[str, Any]", before).get("segments") or []
        if isinstance(entry, dict)
    }
    for entry in settled.get("segments") or []:
        if isinstance(entry, dict) and not had.get(_segment_key(entry), False):
            _ = cast("dict[str, object]", entry).pop(SEGMENT_DRIFT_KEY, None)
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
            for k, v in cast("dict[str, object]", value).items()
        )
    if isinstance(value, list):
        return any(has_key(v, key) for v in cast("list[object]", value))
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
_LEFT: dict[pathlib.Path, str] = {}
"""The backup of each module not yet put back, with the module's name."""


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
        for key, value in cast("dict[str, object]", refs).items()
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
    _LEFT[path] = site.name.value
    return path


def report_leftover_backups(write: Callable[[str], object]) -> None:
    """Say, through *write*, where the backup of each module not put back is; once.

    Only a backup whose file is still there is listed, and the list is then empty.
    """
    left = {path: name for path, name in _LEFT.items() if path.exists()}
    _LEFT.clear()
    if not left:
        return
    _ = write(
        f"live suite: {len(left)} module(s) were not put back; the original of "
        "each is saved at:"
    )
    for path, name in left.items():
        _ = write(f"  {path}  ({name})")


def _print_to_stderr(line: str) -> None:
    print(line, file=sys.stderr)


_ = atexit.register(report_leftover_backups, _print_to_stderr)


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


def refs_as_read(
    module: AnyStoredModule, document: Mapping[str, Any]
) -> dict[str, Any] | MISSING:
    """The refs a write of *document* to *module* carries: the module's refs as read, less the append-only names.

    Only a deviceSettings or QoS module whose data has segments carries them
    (``sends_refs_always``), and only where the refs were read; any other write
    is of data alone. A copy, taken from *module* as it was just read.
    """
    refs = module.raw_refs
    if not sends_refs_always(module, document) or not isinstance(refs, dict):
        return MISSING
    return {
        str(name): copy.deepcopy(rows)
        for name, rows in cast("dict[object, Any]", refs).items()
        if name not in APPEND_ONLY_REF_NAMES
    }


async def write_data(site: Site, module: AnyStoredModule, data: object) -> None:
    """Send *data* to the module as it stands (its version), with the refs a library write of it carries (``refs_as_read``)."""
    document = expect(data, dict, "the data to write")
    _ = await site.client.update_configuration_module(
        UpdateConfigurationModule(
            configuration_module_id=module.id_,
            enterprise_id=(
                site.enterprise_id if site.enterprise_id is not None else MISSING
            ),
            version=module.version or MISSING,
            update=ConfigurationModuleUpdate(
                data=copy.deepcopy(document), refs=refs_as_read(module, document)
            ),
            return_data=True,
        ),
        level=module.config_context,
    )


async def restore(site: Site, original: Original | None) -> None:
    """Put the module back as *original* and check that it is, undoing only what this run did.

    The raw data goes back as written, under the module's current version. A
    module this run inserted is deleted; one it deleted is inserted again; one
    it wrote gets the original data. Where the module is not as the
    Orchestrator last answered this run (another writer changed, added or
    removed it) nothing is written and the test fails.
    """
    run = run_of(site.client)
    name = site.name.value
    current = await site.read()
    if original is None:
        if current is None:
            return
        check(
            current.id_ in run.inserted,
            f"restore: a {name} module this run did not insert is there now; it "
            "was left as it is; check who created it, and delete it by hand if "
            "the lab should not have it",
        )
        await delete_module(site, current.id_)
        check(
            await site.read() is None,
            f"restore: the {name} module the test added is still there",
        )
        return
    if current is None:
        check(
            original.module_id in run.deleted,
            f"restore: the {name} module is gone and this run did not delete it; "
            "it was not put back; check who deleted it, and insert it again "
            "from the saved original if it is wanted",
        )
        await insert_module(site, original.data, original.refs)
    else:
        check(
            current.id_ == original.module_id or current.id_ in run.inserted,
            f"restore: another {name} module is there than the one the test "
            "began with, and this run did not insert it; it was left as it is; "
            "check who replaced the module, and put the original back by hand "
            "if it is wanted",
        )
        expected = run.seen.get(current.id_, _UNKNOWN)
        if expected is not _UNKNOWN:
            outside = changes_between(site.name, expected, current.raw_data)
            check(
                not outside,
                f"restore: the {name} module changed outside this run after its "
                f"last write ({describe(outside)}); it was left as it is, with "
                f"this run's changes; check who else changed the {name} module, "
                "and put the original back by hand if it is wanted",
            )
        if changes_between(site.name, original.data, current.raw_data):
            await write_data(site, current, original.data)
    after = await site.read()
    check(after is not None, f"restore: the {name} module is missing")
    assert after is not None
    changes = changes_between(site.name, original.data, after.raw_data)
    check(
        not changes,
        f"restore: the {name} module differs from the original after "
        f"writing it back: {describe(changes)}",
    )


def ref_names(refs: object) -> list[str]:
    """The names of the refs; their content (IDs, login tokens) is not stable."""
    return sorted(cast("dict[str, object]", refs)) if isinstance(refs, dict) else []


@contextmanager
def interrupts_held() -> Generator[None]:
    """For the length of the block, hold Ctrl-C; deliver it once the block is left.

    Only the main thread takes signals; in another thread nothing is held.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    noted = False

    def note(_signal: int, _frame: object) -> None:
        nonlocal noted
        if not noted:
            print(
                "live suite: holding Ctrl-C until the module is put back",
                file=sys.stderr,
            )
        noted = True

    previous = signal.signal(signal.SIGINT, note)
    try:
        yield
    finally:
        _ = signal.signal(
            signal.SIGINT, previous if previous is not None else signal.SIG_DFL
        )
        if noted:
            signal.raise_signal(signal.SIGINT)


@asynccontextmanager
async def restoring(site: Site) -> AsyncGenerator[Original | None]:
    """Run a test's writes to *site*'s module and, however they end, undo them.

    The block is open for the client's write guard from the backup until the
    restore is done, so that the restore's own writes are covered.
    """
    run = run_of(site.client)
    require_writes(run)
    check(site.target, NOT_A_TARGET)
    original = await capture(site)
    backup = save_backup(site, original) if original is not None else None
    if original is not None:
        run.seen[original.module_id] = copy.deepcopy(original.data)
    entry: Block = (site, original.module_id if original is not None else None)
    run.open.append(entry)
    try:
        try:
            yield original
        finally:
            with interrupts_held():
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
                        f"{what}; the original {site.name.value} module is saved "
                        f"at {backup}",
                        pytrace=False,
                    )
                else:
                    if backup is not None:
                        backup.unlink(missing_ok=True)
                        _ = _LEFT.pop(backup, None)
    finally:
        run.close(entry)


async def own_data_refusal(site: Site) -> str | None:
    """Why the Orchestrator does not take the module's own data back, or ``None`` where it does.

    Some modules of a lab are not valid as they stand (an address missing from a
    LAN network, say) and every write to them is refused, whatever it holds: the
    answer is then the codes of the refusal. A module that is not there is
    answered too. Any other error of the write, the client's own included, is
    raised: it says nothing of the module.

    The write is not free: the Orchestrator may add ``routeMapsVersion`` to a
    deviceSettings segment entry of a module it takes (``settle``). That key is
    accepted drift: it stays, and ``restore`` does not count it.
    """
    async with restoring(site) as original:
        module = await site.read()
        if original is None or module is None:
            return "the module is not there"
        try:
            await write_data(site, module, original.data)
        except DataRefused as refusal:
            return refusal.error
    return None


async def accepts_a_write(site: Site) -> bool:
    """Whether the Orchestrator takes the module's own data back (``own_data_refusal``)."""
    return await own_data_refusal(site) is None


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
    ``__context__``, until a ``ConfigConflictError``. One with a cause of its
    own was raised from the Orchestrator's precondition error, whatever HTTP
    status carried it; one without is a row count of zero. With no such
    conflict in the chain, the refusal is only ``refused``: that is so where a
    read-back failed to parse, as the error is then raised from a pydantic
    ``ValidationError``, which has no ``__context__`` to lead to the conflict.
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
        value = cast("dict[str, object]", value).get(part, MISSING)
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
# What the Orchestrator answered the client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """One answer of the Orchestrator, as the client received it.

    The body is lab data. It stays in memory: a test reads it through
    ``answer_form`` and never prints it, and the repr of an answer leaves it out.
    """

    method: str
    status: int
    body: bytes = field(repr=False)


def _method_of(content: str | None) -> str:
    """The JSON-RPC method of a request body, or ``""`` where it cannot be read."""
    try:
        request = cast("object", json.loads(content or ""))
    except ValueError:
        return ""
    if not isinstance(request, dict):
        return ""
    method = cast("dict[str, object]", request).get("method")
    return method if isinstance(method, str) else ""


def record_answers(client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> list[Answer]:
    """Record, in the list returned, every answer *client* receives from now on.

    It wraps the request the client sends over HTTP, which is behind the write
    guard of ``run_of``: a write the guard refuses is never sent, so never
    recorded. The test's ``monkeypatch`` puts the client back at its end.
    """
    answers: list[Answer] = []
    send = client._send_request  # pyright: ignore[reportPrivateUsage]

    async def recording(
        http_method: str,
        url: str,
        *,
        params: dict[str, object] | None = None,
        json_data: dict[str, object] | None = None,
        content: str | None = None,
    ) -> httpx.Response:
        response = await send(
            http_method, url, params=params, json_data=json_data, content=content
        )
        answers.append(
            Answer(_method_of(content), response.status_code, response.content)
        )
        return response

    monkeypatch.setattr(client, "_send_request", recording)
    return answers


def answer_form(answer: Answer) -> str:
    """How *answer* answers a module update, with its HTTP status and nothing of its body.

    The body holds names and IDs of the lab, so only its form is said: a
    refusal by the precondition error, another error, the module row, or a
    row count (the count is a number and nothing else is read from it).
    """
    status = f"HTTP {answer.status}"
    text = answer.body.decode(errors="replace")
    if is_precondition_answer(text):
        return f"refused with the precondition error ({status})"
    try:
        document = cast("object", json.loads(text))
    except ValueError:
        return f"an answer that is not JSON ({status})"
    if isinstance(document, dict):
        fields = cast("dict[str, object]", document)
        if isinstance(fields.get("error"), dict):
            return f"refused with another error ({status})"
        result = fields.get("result")
        if isinstance(result, dict):
            row = cast("dict[str, object]", result)
            if "data" in row:
                return f"not refused: the module row ({status})"
            rows = row.get("rows")
            if isinstance(rows, int) and not isinstance(rows, bool):
                return f"not refused: a row count of {rows} ({status})"
    return f"an answer of another shape ({status})"


def without_return_data(client: VcoClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send *client*'s module updates and inserts without ``returnData``.

    An Orchestrator then answers with a row count, as one that ignores
    ``returnData`` does. The wrappers are over the client's methods as they are
    now, the guard of ``run_of`` among them, so the guard still refuses and
    counts every write before anything is sent. The request passed in is
    changed.
    """
    update = client.update_configuration_module
    insert = client.insert_configuration_module

    async def update_without(
        request: UpdateConfigurationModule, *, level: ConfigContext | None = None
    ) -> AnyStoredModule | None:
        request.return_data = MISSING
        return await update(request, level=level)

    async def insert_without(
        request: InsertConfigurationModule, *, level: ConfigContext | None = None
    ) -> InsertConfigurationModuleResult | AnyStoredModule:
        request.return_data = MISSING
        return await insert(request, level=level)

    monkeypatch.setattr(client, "update_configuration_module", update_without)
    monkeypatch.setattr(client, "insert_configuration_module", insert_without)


def read_backs(answers: Sequence[Answer]) -> int:
    """How many of *answers* are to ``getConfiguration``, the request ``write_module`` reads a module back with.

    An edge site and an edge load of the pipeline read the configuration stack
    and are not counted. Whichever method of the client sent the request, it
    counts.
    """
    return sum(answer.method == "configuration/getConfiguration" for answer in answers)


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
        listed = cast("list[object]", entries)
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
    if "lab" not in cast("list[str]", getattr(item, "fixturenames", [])):
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
