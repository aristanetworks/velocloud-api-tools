"""Support for the live configuration-save tests: reading, restoring and redacting.

The tests that use this write to a real Orchestrator, so everything here is
about leaving it as it was found. Nothing names a lab entity: targets are found
at run time, and every message refers to them by position and module type only.

pyvelo-intent's live tests use the same file, copied to
``packages/pyvelo-intent/tests/integration/_intent_live_support.py``; the two
must stay identical (``cp`` one over the other).

``restoring`` is the one way a test writes: it records a module's raw data first
and, however the test ends, puts it back and checks that it is back. A module
that did not exist is deleted; one that was deleted is inserted again.

What the Orchestrator does to a module as it stores it, seen on a lab:

* It adds ``routeMapsVersion`` to a deviceSettings segment that lacks it
  (``SERVER_ADDED_KEYS``). A module is compared without such keys.
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

import copy
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


async def list_edge_ids(client: VcoClient, enterprise_id: int | None) -> list[int]:
    edges = await client.get_enterprise_edges(
        GetEnterpriseEdges(enterprise_id=enterprise_id if enterprise_id else MISSING)
    )
    return [e.id_ for e in edges if isinstance(e.id_, int)]


async def list_profile_ids(client: VcoClient, enterprise_id: int | None) -> list[int]:
    profiles = await client.get_enterprise_configurations(
        GetEnterpriseConfigurations(
            enterprise_id=enterprise_id if enterprise_id else MISSING
        )
    )
    return [p.id_ for p in profiles if isinstance(p.id_, int)]


def is_known_load_gap(exc: BaseException) -> bool:
    """Whether *exc* is the model gap on a client-connector reference.

    A deviceSettings ``clientConnector`` ref that arrives as one object with
    ``data: null`` does not validate. Every error of such a failure is located
    inside a ``clientConnector`` key.
    """
    if not isinstance(exc, ValidationError):
        return False
    errors = exc.errors()
    return bool(errors) and all(
        any("clientConnector" in str(part) for part in error["loc"]) for error in errors
    )


async def discover_layouts(
    client: VcoClient, enterprise_id: int | None
) -> tuple[list[EdgeLayout], list[int]]:
    """Every edge that loads, and the positions of those that do not.

    A failure other than the known load gap is raised: a lab that this suite
    cannot read is not a lab it can test.
    """
    layouts: list[EdgeLayout] = []
    unloadable: list[int] = []
    for index, edge_id in enumerate(await list_edge_ids(client, enterprise_id)):
        try:
            edge, profile = await read_stack(client, edge_id, enterprise_id)
        except ValidationError as exc:
            if not is_known_load_gap(exc):
                raise
            unloadable.append(index)
            continue
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
    return layouts, unloadable


@dataclass(frozen=True)
class Lab:
    """What the Orchestrator has, as found by one read of every edge and profile."""

    client: VcoClient
    enterprise_id: int | None
    layouts: list[EdgeLayout]
    unloadable: list[int]
    """Positions of the edges that do not load (the known model gap)."""
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

    def pick_edge(
        self,
        name: ConfigurationModuleName,
        suitable: Callable[[AnyConfigurationModule], bool] = lambda _: True,
        what: str = "",
    ) -> EdgeLayout:
        """The first such edge, or skip the test: the lab has no target for it."""
        found = self.edges_with(name, suitable)
        if not found:
            pytest.skip(f"no edge has a {name.value} module {what or 'to use'}")
        return found[0]

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


async def discover_lab(client: VcoClient, enterprise_id: int | None) -> Lab:
    layouts, unloadable = await discover_layouts(client, enterprise_id)
    return Lab(
        client=client,
        enterprise_id=enterprise_id,
        layouts=layouts,
        unloadable=unloadable,
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
        assert module is not None, f"the {self.name} module is gone"
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
    result = await client.portal("edge/getEdgeConfigurationModules", request, RawJson)
    module = result.root[name.value]
    assert isinstance(module, dict)
    data = cast(dict[str, Any], module)["data"]
    assert isinstance(data, dict)
    return cast(dict[str, Any], data)


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
    result = await site.client.portal(
        "configuration/deleteConfigurationModule", request, RowsModifiedConfirmation
    )
    assert not result.error, "deleting a module the test inserted failed"


async def insert_module(site: Site, data: object, refs: object) -> None:
    """Insert a module (firewall or QoS only) with *data* exactly as given."""
    assert isinstance(data, dict)
    enterprise = site.enterprise_id if site.enterprise_id is not None else MISSING
    if site.name is ConfigurationModuleName.FIREWALL:
        _ = await site.client.insert_configuration_module(
            InsertFirewallConfigurationModule(
                configuration_id=site.configuration_id,
                data=copy.deepcopy(data),  # pyright: ignore[reportUnknownArgumentType]
                enterprise_id=enterprise,
            )
        )
    elif site.name is ConfigurationModuleName.QOS:
        _ = await site.client.insert_configuration_module(
            InsertQosConfigurationModule(
                configuration_id=site.configuration_id,
                data=copy.deepcopy(data),  # pyright: ignore[reportUnknownArgumentType]
                refs=copy.deepcopy(refs) if isinstance(refs, dict) else None,  # pyright: ignore[reportUnknownArgumentType]
                enterprise_id=enterprise,
            )
        )
    else:
        raise AssertionError(f"a {site.name} module cannot be inserted")


@dataclass(frozen=True)
class Original:
    """A module as the test found it."""

    data: object
    refs: object
    """Only to insert the module again if a test deleted it."""


SERVER_ADDED_KEYS = frozenset({"routeMapsVersion"})
"""Keys the Orchestrator adds to a module as it stores it (see the module docstring)."""


def plain(value: object) -> object:
    """A deep copy of *value* without the keys the Orchestrator adds on a write."""
    if isinstance(value, dict):
        return {
            k: plain(v)
            for k, v in cast(dict[str, object], value).items()
            if k not in SERVER_ADDED_KEYS
        }
    if isinstance(value, list):
        return [plain(v) for v in cast(list[object], value)]
    return copy.deepcopy(value)


def plain_changes(
    old: object, new: object, model: type[VcoModel] | None = None
) -> list[RawChange]:
    """``raw_changes`` of two module documents, as if neither had the server-added keys."""
    return raw_changes(plain(old), plain(new), model)


async def capture(site: Site) -> Original | None:
    """The raw data and refs of the module now, or None if the layer lacks it."""
    module = await site.read()
    if module is None:
        return None
    return Original(
        data=copy.deepcopy(module.raw_data), refs=copy.deepcopy(module.raw_refs)
    )


async def write_data(site: Site, module: AnyConfigurationModule, data: object) -> None:
    """Send *data* to the module as it stands (its version), with no refs."""
    assert isinstance(data, dict)
    await site.client.update_configuration_module(
        UpdateConfigurationModule(
            configuration_module_id=module.id_,
            enterprise_id=(
                site.enterprise_id if site.enterprise_id is not None else MISSING
            ),
            version=module.version or MISSING,
            update=ConfigurationModuleUpdate(data=copy.deepcopy(data)),  # pyright: ignore[reportUnknownArgumentType]
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
            f"restore: the {site.name} module the test added is still there",
        )
        return
    if current is None:
        await insert_module(site, original.data, original.refs)
    elif plain(current.raw_data) != plain(original.data):
        await write_data(site, current, original.data)
    after = await site.read()
    check(after is not None, f"restore: the {site.name} module is missing")
    assert after is not None
    changes = plain_changes(original.data, after.raw_data)
    check(
        not changes,
        f"restore: the {site.name} module differs from the original after "
        f"writing it back: {describe(changes)}",
    )


def ref_names(refs: object) -> list[str]:
    """The names of the refs; their content (IDs, login tokens) is not stable."""
    return sorted(cast(dict[str, object], refs)) if isinstance(refs, dict) else []


@asynccontextmanager
async def restoring(site: Site) -> AsyncGenerator[Original | None]:
    """Run a test's writes to *site*'s module and, however they end, undo them."""
    original = await capture(site)
    try:
        yield original
    finally:
        await restore(site, original)


async def accepts_a_write(site: Site) -> bool:
    """Whether the Orchestrator takes the module's own data back.

    Some modules of a lab are not valid as they stand (an address missing from a
    LAN network, say) and every write to them is refused, whatever it holds.
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
