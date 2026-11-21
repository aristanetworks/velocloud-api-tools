"""Support for the live configuration-save tests: reading, restoring and redacting.

The tests that use this write to a real Orchestrator, so everything here is
about leaving it as it was found. Nothing names a lab entity: targets are found
at run time, and every message refers to them by position and module type only.

pyvelo-intent's live tests use the same file, copied to
``packages/pyvelo-intent/tests/integration/_intent_live_support.py``; the two
must stay identical (``cp`` one over the other).

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
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, cast

import pytest
from pydantic import MISSING, RootModel, ValidationError
from pyvelo.client import VcoClient, VcoOperationError
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleUpdate,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    GetConfiguration,
    InsertConfigurationModule,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.client.schemas.edge import (
    GetEdgeConfigurationModules,
    GetEdgeConfigurationStack,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.core import Alias, NoneIsMissing, RowsModifiedConfirmation, VcoModel
from pyvelo.core.rebase import Key, Path, RawChange, raw_changes

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


class LabUnreadable(Exception):
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


def _redacted(exc: VcoOperationError) -> VcoOperationError:
    return VcoOperationError(exc.operation, refusal_codes(exc), code=exc.code)


def refusal_codes(exc: VcoOperationError) -> str:
    """The error codes and fields of a refusal; the rest of its text names lab entities."""
    found = re.findall(r"\[([A-Z][A-Z0-9_]*)\]\s*([\w.\[\]]*)", str(exc))
    return (
        "; ".join(f"{code} {field}".strip() for code, field in found) or "no error code"
    )


_REDACTED: set[int] = set()


def redact_refusals(client: VcoClient) -> None:
    """Make the client's write refusals carry their codes alone.

    The Orchestrator's validation text quotes the names of what it refuses, and a
    refused write in a test would put them in the test output.
    """
    if id(client) in _REDACTED:
        return
    update = client.update_configuration_module
    insert = client.insert_configuration_module

    async def redacted_update(request: UpdateConfigurationModule) -> None:
        try:
            await update(request)
        except VcoOperationError as exc:
            raise _redacted(exc) from None

    async def redacted_insert(request: InsertConfigurationModule) -> Any:
        try:
            return await insert(request)
        except VcoOperationError as exc:
            raise _redacted(exc) from None

    # Instance attributes, which shadow the methods: a spy a test puts over them
    # (``monkeypatch.setattr``) wraps these.
    attributes = vars(client)
    attributes["update_configuration_module"] = redacted_update
    attributes["insert_configuration_module"] = redacted_insert
    _ = _REDACTED.add(id(client))


async def discover_lab(client: VcoClient, enterprise_id: int | None) -> Lab:
    redact_refusals(client)
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
    await site.client.update_configuration_module(
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
    """Run a test's writes to *site*'s module and, however they end, undo them."""
    original = await capture(site)
    backup = save_backup(site, original) if original is not None else None
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
                if isinstance(exc, pytest.fail.Exception)
                else type(exc).__name__
            )
            pytest.fail(
                f"{what}; the original {site.name.value} module is saved at {backup}",
                pytrace=False,
            )
        else:
            if backup is not None:
                backup.unlink(missing_ok=True)


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
