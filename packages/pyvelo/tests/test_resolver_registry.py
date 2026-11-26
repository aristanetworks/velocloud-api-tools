"""Tests for ResolverContext, Resolver protocol, and ResolverRegistry."""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar, assert_type, cast, override

import pytest
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.config.resolver import (
    BaseResolver,
    Resolver,
    ResolverContext,
    ResolverRegistry,
)
from pyvelo.config.resolvers.address_group import AddressGroupResolver
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.resolvers.license import LicenseResolver
from pyvelo.config.resolvers.link import LinkResolver
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.resolvers.service_group import ServiceGroupResolver
from pyvelo.core.identity import (
    HubIdentity,
    HubName,
    IdentityKey,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentId,
    SegmentIdentity,
    SegmentName,
)

from ._type_support import SegmentResolverFactory

# ---------------------------------------------------------------------------
# Test identity types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FooIdentity:
    name: str
    id: int
    logical_id: uuid.UUID


@dataclass(frozen=True)
class BarIdentity:
    name: str
    id: int


# ---------------------------------------------------------------------------
# In-memory mock resolvers
# ---------------------------------------------------------------------------


def _no_client() -> VcoClientProtocol:
    """A client for a resolver that never calls the VCO."""
    return cast("VcoClientProtocol", cast("object", None))


@dataclass(frozen=True)
class FooName(IdentityKey[FooIdentity]):
    value: str


@dataclass(frozen=True)
class FooId(IdentityKey[FooIdentity]):
    value: int


@dataclass(frozen=True)
class BarName(IdentityKey[BarIdentity]):
    value: str


FOO = FooIdentity(name="foo", id=1, logical_id=uuid.UUID(int=1))


class MockFooResolver(BaseResolver[FooIdentity]):
    """Overrides ``lookup`` and ``lookup_key`` only, over a table ``load`` fills."""

    identity_type: type[FooIdentity] = FooIdentity

    def __init__(self) -> None:
        super().__init__(_no_client())
        self._data: dict[str, FooIdentity] = {}
        self.load_count: int = 0

    @override
    def lookup(self, name: str) -> FooIdentity:
        if name not in self._data:
            raise KeyError(f"Unknown Foo: {name!r}")
        return self._data[name]

    @override
    def lookup_key(self, key: IdentityKey[FooIdentity]) -> FooIdentity:
        match key:
            case FooName(value=name):
                return self.lookup(name)
            case FooId(value=value):
                return next(i for i in self._data.values() if i.id == value)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        _ = context
        self.load_count += 1
        self._data = {
            "alpha": FooIdentity(name="alpha", id=1, logical_id=uuid.UUID(int=1)),
            "beta": FooIdentity(name="beta", id=2, logical_id=uuid.UUID(int=2)),
        }


class MockBarResolver(BaseResolver[BarIdentity]):
    identity_type: type[BarIdentity] = BarIdentity

    def __init__(self) -> None:
        super().__init__(_no_client())
        self._data: dict[str, BarIdentity] = {}

    @override
    def lookup(self, name: str) -> BarIdentity:
        if name not in self._data:
            raise KeyError(f"Unknown Bar: {name!r}")
        return self._data[name]

    @override
    def lookup_key(self, key: IdentityKey[BarIdentity]) -> BarIdentity:
        match key:
            case BarName(value=name):
                return self.lookup(name)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        _ = context
        self._data = {
            "first": BarIdentity(name="first", id=10),
            "second": BarIdentity(name="second", id=20),
        }


class NoCopyResolver:
    """A resolver written against the protocol alone: it has no ``unloaded()``."""

    identity_type: type[BarIdentity] = BarIdentity
    requires_load: ClassVar[bool] = False
    loaded: bool = True

    def lookup(self, name: str) -> BarIdentity:
        return BarIdentity(name=name, id=1)

    def lookup_key(self, key: IdentityKey[BarIdentity]) -> BarIdentity:
        _ = key
        raise NotImplementedError

    async def resolve(self, name: str) -> BarIdentity:
        return self.lookup(name)

    async def resolve_key(self, key: IdentityKey[BarIdentity]) -> BarIdentity:
        return self.lookup_key(key)

    async def load(self, context: ResolverContext) -> None:
        _ = context

    def identities(self) -> list[BarIdentity]:
        return []


class WrongIdentityResolver(BaseResolver[FooIdentity]):
    """The type checker refuses a wrong ``identity_type``.

    One that is not the resolver's identity.
    """

    identity_type = BarIdentity  # pyright: ignore[reportAssignmentType, reportUnannotatedClassAttribute]

    @override
    async def _load(self, context: ResolverContext) -> None:
        _ = context


class CountingResolver(BaseResolver[FooIdentity]):
    """Registers ``alpha`` on each load, or fails when told to."""

    identity_type: type[FooIdentity] = FooIdentity

    def __init__(self) -> None:
        super().__init__(_no_client())
        self.fail: bool = False

    @override
    async def _load(self, context: ResolverContext) -> None:
        _ = context
        if self.fail:
            raise RuntimeError("the VCO did not answer")
        self._clear()
        self._register("alpha", FooIdentity("alpha", 1, uuid.UUID(int=1)), id=1)


class OnlyLookupsResolver(BaseResolver[FooIdentity]):
    """A resolver that defines the two lookups and nothing awaitable."""

    identity_type: type[FooIdentity] = FooIdentity

    def __init__(self) -> None:
        super().__init__(_no_client())

    @override
    def lookup(self, name: str) -> FooIdentity:
        _ = name
        return FOO

    @override
    def lookup_key(self, key: IdentityKey[FooIdentity]) -> FooIdentity:
        _ = key
        return FOO


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolverContext:
    def test_defaults(self) -> None:
        ctx = ResolverContext()
        assert ctx.enterprise_id is None
        assert ctx.edge_id is None
        assert ctx.configuration_id is None

    def test_with_values(self) -> None:
        ctx = ResolverContext(enterprise_id=1, edge_id=2, configuration_id=3)
        assert ctx.enterprise_id == 1
        assert ctx.edge_id == 2
        assert ctx.configuration_id == 3


class TestResolverProtocol:
    def test_mock_satisfies_protocol(self) -> None:
        resolver = MockFooResolver()
        assert isinstance(resolver, Resolver)


class TestResolverRegistry:
    @pytest.fixture
    def registry(self) -> ResolverRegistry:
        return ResolverRegistry()

    @pytest.fixture
    def foo_resolver(self) -> MockFooResolver:
        return MockFooResolver()

    @pytest.fixture
    def bar_resolver(self) -> MockBarResolver:
        return MockBarResolver()

    def test_register_and_set_context(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        registry.register(foo_resolver)
        ctx = ResolverContext(enterprise_id=1)
        registry.set_context(ctx)
        assert registry.current_context is ctx

    async def test_resolve(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        result = await registry.resolve(FooIdentity, "alpha")
        assert result.id == 1
        assert result.logical_id == uuid.UUID(int=1)

    async def test_resolve_unknown_identity_type(
        self, registry: ResolverRegistry
    ) -> None:
        with pytest.raises(LookupError, match="No resolver registered") as caught:
            _ = await registry.resolve(FooIdentity, "anything")
        assert not isinstance(caught.value, ResolutionError)

    async def test_resolve_unknown_name(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        with pytest.raises(KeyError, match="Unknown Foo"):
            _ = await registry.resolve(FooIdentity, "nonexistent")

    async def test_multiple_resolvers(
        self,
        registry: ResolverRegistry,
        foo_resolver: MockFooResolver,
        bar_resolver: MockBarResolver,
    ) -> None:
        registry.register(foo_resolver)
        registry.register(bar_resolver)
        await foo_resolver.load(ResolverContext())
        await bar_resolver.load(ResolverContext())

        foo = await registry.resolve(FooIdentity, "alpha")
        bar = await registry.resolve(BarIdentity, "first")
        assert foo.id == 1
        assert bar.id == 10

    def test_context_default_none(self, registry: ResolverRegistry) -> None:
        assert registry.current_context is None

    def test_context_manager_scopes_then_restores(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        registry.register(foo_resolver)
        outer = ResolverContext(enterprise_id=1)
        inner = ResolverContext(enterprise_id=2)
        registry.set_context(outer)
        with registry.context(inner) as bound:
            assert bound is inner
            assert registry.current_context is inner
        assert registry.current_context is outer

    def test_context_manager_restores_on_exception(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        registry.register(foo_resolver)
        outer = ResolverContext(enterprise_id=1)
        registry.set_context(outer)
        with (
            pytest.raises(RuntimeError),
            registry.context(ResolverContext(enterprise_id=2)),
        ):
            raise RuntimeError("boom")
        assert registry.current_context is outer

    async def test_context_manager_clears_caches(
        self, registry: ResolverRegistry
    ) -> None:
        # Register a real BaseResolver-style resolver so _clear is exercised.
        class CountedResolver(BaseResolver[FooIdentity]):
            identity_type: type[FooIdentity] = FooIdentity

            def __init__(self) -> None:
                super().__init__(client=_no_client())
                self.cleared: int = 0

            @override
            def _clear(self) -> None:
                self.cleared += 1
                super()._clear()

            @override
            async def _load(self, context: ResolverContext) -> None:
                _ = context
                self._register(
                    "alpha",
                    FooIdentity(name="alpha", id=1, logical_id=uuid.UUID(int=1)),
                    id=1,
                )

        resolver = CountedResolver()
        registry.register(resolver)
        await resolver.load(ResolverContext())
        assert await resolver.resolve("alpha")
        with registry.context(ResolverContext(enterprise_id=99)):
            assert resolver.cleared == 1
            with pytest.raises(KeyError, match="Unknown foo"):
                _ = await resolver.resolve("alpha")

    def test_resolver_for_gives_the_resolver_of_an_identity_type_or_none(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ) -> None:
        assert registry.resolver_for(FooIdentity) is None
        registry.register(foo_resolver)
        assert registry.resolver_for(FooIdentity) is foo_resolver
        assert registry.resolver_for(BarIdentity) is None

    async def test_unloaded_copies_each_resolver_with_nothing_loaded(
        self,
        registry: ResolverRegistry,
        make_segment_resolver: SegmentResolverFactory,
    ) -> None:
        loaded = await make_segment_resolver([{"name": "Guest", "segment_id": 1}])
        registry.register(loaded)
        registry.set_context(ResolverContext(enterprise_id=1))

        copy = registry.unloaded()

        assert copy.current_context is None
        twin = copy.get_resolver(SegmentResolver)
        assert twin is not loaded
        assert list(twin.identities()) == []
        assert [i.name for i in loaded.identities()] == ["Guest"]
        await twin.load(ResolverContext())  # the same client answers
        with registry.context(ResolverContext(enterprise_id=9)):  # clears the original
            assert list(loaded.identities()) == []
            assert [i.name for i in twin.identities()] == ["Guest"]

    def test_unloaded_refuses_a_resolver_it_cannot_copy(
        self, registry: ResolverRegistry
    ) -> None:
        registry.register(NoCopyResolver())
        with pytest.raises(
            TypeError, match=r"NoCopyResolver has no unloaded\(\) method"
        ):
            _ = registry.unloaded()


@pytest.fixture
async def segment_registry(
    make_segment_resolver: SegmentResolverFactory,
) -> ResolverRegistry:
    registry = ResolverRegistry()
    registry.register(await make_segment_resolver([{"name": "Guest", "segment_id": 1}]))
    return registry


async def test_resolve_returns_the_key_s_identity_type(
    segment_registry: ResolverRegistry,
) -> None:
    found = await segment_registry.resolve(SegmentName("Guest"))
    _ = assert_type(found, SegmentIdentity)
    assert found.segment_id == 1


def test_lookup_needs_no_event_loop(segment_registry: ResolverRegistry) -> None:
    # a plain (not async) test: nothing is awaited
    assert segment_registry.lookup(SegmentId(1)).name == "Guest"
    assert segment_registry.lookup(SegmentIdentity, "guest").name == "Guest"
    _ = assert_type(segment_registry.lookup(SegmentId(1)), SegmentIdentity)


async def test_resolve_and_lookup_agree(segment_registry: ResolverRegistry) -> None:
    assert await segment_registry.resolve(SegmentId(1)) == segment_registry.lookup(
        SegmentId(1)
    )


def test_lookup_tells_no_resolver_from_not_loaded() -> None:
    registry = ResolverRegistry()
    with pytest.raises(LookupError) as no_resolver:
        _ = registry.lookup(SegmentName("x"))
    assert not isinstance(no_resolver.value, ResolutionError)
    registry.register(HubResolver(_no_client()))
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.lookup(HubName("x"))


def test_lookup_by_finds_a_loaded_identity_by_any_field(
    segment_registry: ResolverRegistry,
) -> None:
    # the fixture's Guest segment has object_id 1 (it follows segment_id)
    found = segment_registry.lookup_by(SegmentIdentity, lambda s: s.object_id, 1)
    _ = assert_type(found, SegmentIdentity)
    assert found.name == "Guest"


def test_lookup_by_refuses_an_unknown_value_and_an_unloaded_resolver(
    segment_registry: ResolverRegistry,
) -> None:
    with pytest.raises(ResolutionError) as unknown:
        _ = segment_registry.lookup_by(SegmentIdentity, lambda s: s.object_id, 999)
    assert not isinstance(unknown.value, ResolverNotLoadedError)
    assert unknown.value.name == "999"
    assert unknown.value.identity_type is SegmentIdentity
    assert unknown.value.candidates == []
    with pytest.raises(ResolverNotLoadedError):
        _ = ResolverRegistry().lookup_by(SegmentIdentity, lambda s: s.object_id, 1)
    hubs = ResolverRegistry()
    hubs.register(HubResolver(_no_client()))
    with pytest.raises(ResolverNotLoadedError):
        _ = hubs.lookup_by(HubIdentity, lambda h: h.edge_id, 5)


def test_the_attribute_name_lookups_are_gone() -> None:
    assert not hasattr(ResolverRegistry, "identity_sync")
    assert not hasattr(ResolverRegistry, "has_id_sync")


def test_the_string_keyed_and_coroutine_driving_methods_are_gone() -> None:
    assert not hasattr(ResolverRegistry, "resolve_sync")
    assert not hasattr(ResolverRegistry, "reverse")
    assert not hasattr(BaseResolver, "reverse_by")


def test_a_resolver_that_defines_only_the_lookups_resolves() -> None:
    resolver = OnlyLookupsResolver()
    assert asyncio.run(resolver.resolve("foo")) == FOO
    assert asyncio.run(resolver.resolve_key(FooName("foo"))) == FOO


async def test_a_load_sets_loaded_and_a_failed_load_leaves_it() -> None:
    resolver = CountingResolver()
    assert not resolver.loaded

    await resolver.load(ResolverContext())

    assert resolver.loaded
    assert not resolver.unloaded().loaded
    resolver.fail = True
    with pytest.raises(RuntimeError):
        await resolver.load(ResolverContext())
    assert resolver.loaded  # the tables of the load before are still there
    assert [i.name for i in resolver.identities()] == ["alpha"]


async def test_a_cleared_resolver_is_not_loaded() -> None:
    resolver = CountingResolver()
    registry = ResolverRegistry()
    registry.register(resolver)
    await resolver.load(ResolverContext())

    with registry.context(ResolverContext(enterprise_id=2)):
        assert not resolver.loaded
        assert list(resolver.identities()) == []


def test_no_stock_resolver_overrides_load() -> None:
    stock: list[type[BaseResolver[Any]]] = [
        AddressGroupResolver,
        ApplicationResolver,
        EdgeResolver,
        HubResolver,
        LicenseResolver,
        LinkResolver,
        NetworkServiceResolver,
        ProfileResolver,
        SegmentResolver,
        ServiceGroupResolver,
    ]
    for resolver in stock:
        assert "load" not in vars(resolver), resolver.__name__
        assert "_load" in vars(resolver), resolver.__name__


class DupResolver(BaseResolver[FooIdentity]):
    """Two identities share the name ``dup``; the base keeps one per name."""

    identity_type: type[FooIdentity] = FooIdentity

    def __init__(self) -> None:
        super().__init__(_no_client())

    @override
    def lookup_key(self, key: IdentityKey[FooIdentity]) -> FooIdentity:
        match key:
            case FooName(value=name):
                return self.lookup(name)
            case FooId(value=value):
                return self._by_key("id", value)
            case _:
                raise TypeError(f"Unsupported key type: {type(key).__name__}")

    @override
    async def _load(self, context: ResolverContext) -> None:
        _ = context
        self._clear()
        for number in (1, 2):
            identity = FooIdentity("dup", number, uuid.UUID(int=number))
            self._register(identity.name, identity, id=identity.id)

    def typo(self) -> FooIdentity:
        return self._by_key("idd", 1)

    def typo_at_registration(self) -> None:
        self._register("x", FOO, idd=1)


async def dup_registry() -> tuple[ResolverRegistry, DupResolver]:
    resolver = DupResolver()
    await resolver.load(ResolverContext())
    registry = ResolverRegistry()
    registry.register(resolver)
    return registry, resolver


async def test_an_id_key_returns_the_identity_registered_under_that_id() -> None:
    registry, _ = await dup_registry()

    assert registry.lookup(FooId(1)).id == 1
    assert registry.lookup(FooId(2)).id == 2
    with pytest.raises(ResolutionError, match=r"No foo has id 3\."):
        _ = registry.lookup(FooId(3))


async def test_an_attribute_that_is_no_field_of_the_identity_is_a_type_error() -> None:
    _, resolver = await dup_registry()

    with pytest.raises(TypeError, match="FooIdentity has no field 'idd'"):
        _ = resolver.typo()
    with pytest.raises(TypeError, match="FooIdentity has no field 'idd'"):
        resolver.typo_at_registration()


def test_lookup_by_names_the_label_and_the_value_it_did_not_find(
    segment_registry: ResolverRegistry,
) -> None:
    with pytest.raises(ResolutionError) as unknown:
        _ = segment_registry.lookup_by(SegmentIdentity, lambda s: s.object_id, 999)

    assert str(unknown.value) == "No segment has the value 999."
    assert unknown.value.name == "999"


def test_find_returns_none_where_no_identity_has_the_name_or_the_id(
    segment_registry: ResolverRegistry,
) -> None:
    guest = segment_registry.lookup(SegmentIdentity, "Guest")

    assert segment_registry.find(SegmentIdentity, "guest") == guest
    by_id = segment_registry.find(SegmentId(guest.segment_id))
    _ = assert_type(by_id, SegmentIdentity | None)
    assert by_id == guest
    assert segment_registry.find(SegmentIdentity, "nope") is None
    assert segment_registry.find(SegmentId(999)) is None


def test_find_raises_what_lookup_raises_for_anything_but_a_miss() -> None:
    with pytest.raises(LookupError):
        _ = ResolverRegistry().find(SegmentName("x"))
    hubs = ResolverRegistry()
    hubs.register(HubResolver(_no_client()))
    with pytest.raises(ResolverNotLoadedError):
        _ = hubs.find(HubName("x"))


def test_the_registry_lists_the_loaded_identities_of_a_type(
    segment_registry: ResolverRegistry,
) -> None:
    names = sorted(i.name for i in segment_registry.identities(SegmentIdentity))

    assert "Guest" in names
    assert list(segment_registry.identities(HubIdentity)) == []  # no resolver
    hubs = ResolverRegistry()
    hubs.register(HubResolver(_no_client()))
    assert list(hubs.identities(HubIdentity)) == []  # not loaded
