"""Tests for ResolverContext, Resolver protocol, and ResolverRegistry."""

import uuid
from dataclasses import dataclass
from typing import ClassVar, cast, override

import pytest
from pyvelo.config.resolver import Resolver, ResolverContext, ResolverRegistry

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


class MockFooResolver:
    identity_type: type[FooIdentity] = FooIdentity

    def __init__(self) -> None:
        self._data: dict[str, FooIdentity] = {}
        self.load_count: int = 0

    async def resolve(self, name: str) -> FooIdentity:
        if name not in self._data:
            raise KeyError(f"Unknown Foo: {name!r}")
        return self._data[name]

    async def resolve_key(self, key: object) -> FooIdentity:
        _ = key
        raise NotImplementedError

    async def reverse_by(self, id_attr: str, value: int | str | uuid.UUID) -> str:
        for name, identity in self._data.items():
            if getattr(identity, id_attr) == value:
                return name
        raise KeyError(f"No Foo with {id_attr}={value}")

    async def load(self, context: ResolverContext) -> None:
        _ = context
        self.load_count += 1
        self._data = {
            "alpha": FooIdentity(name="alpha", id=1, logical_id=uuid.UUID(int=1)),
            "beta": FooIdentity(name="beta", id=2, logical_id=uuid.UUID(int=2)),
        }


class MockBarResolver:
    identity_type: type[BarIdentity] = BarIdentity

    def __init__(self) -> None:
        self._data: dict[str, BarIdentity] = {}

    async def resolve(self, name: str) -> BarIdentity:
        if name not in self._data:
            raise KeyError(f"Unknown Bar: {name!r}")
        return self._data[name]

    async def resolve_key(self, key: object) -> BarIdentity:
        _ = key
        raise NotImplementedError

    async def reverse_by(self, id_attr: str, value: int | str | uuid.UUID) -> str:
        for name, identity in self._data.items():
            if getattr(identity, id_attr) == value:
                return name
        raise KeyError(f"No Bar with {id_attr}={value}")

    async def load(self, context: ResolverContext) -> None:
        _ = context
        self._data = {
            "first": BarIdentity(name="first", id=10),
            "second": BarIdentity(name="second", id=20),
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolverContext:
    def test_defaults(self):
        ctx = ResolverContext()
        assert ctx.enterprise_id is None
        assert ctx.edge_id is None
        assert ctx.configuration_id is None

    def test_with_values(self):
        ctx = ResolverContext(enterprise_id=1, edge_id=2, configuration_id=3)
        assert ctx.enterprise_id == 1
        assert ctx.edge_id == 2
        assert ctx.configuration_id == 3


class TestResolverProtocol:
    def test_mock_satisfies_protocol(self):
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
    ):
        registry.register(foo_resolver)
        ctx = ResolverContext(enterprise_id=1)
        registry.set_context(ctx)
        assert registry.current_context is ctx

    async def test_resolve(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ):
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        result = await registry.resolve(FooIdentity, "alpha")
        assert result.id == 1
        assert result.logical_id == uuid.UUID(int=1)

    async def test_resolve_unknown_identity_type(self, registry: ResolverRegistry):
        with pytest.raises(LookupError, match="No resolver registered"):
            _ = await registry.resolve(FooIdentity, "anything")

    async def test_resolve_unknown_name(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ):
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        with pytest.raises(KeyError, match="Unknown Foo"):
            _ = await registry.resolve(FooIdentity, "nonexistent")

    async def test_reverse(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ):
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        name = await registry.reverse(FooIdentity, "id", 1)
        assert name == "alpha"

    async def test_reverse_unknown_identity_type(self, registry: ResolverRegistry):
        with pytest.raises(LookupError, match="No resolver registered"):
            _ = await registry.reverse(FooIdentity, "id", 1)

    async def test_reverse_by_logical_id(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ):
        registry.register(foo_resolver)
        await foo_resolver.load(ResolverContext())
        name = await registry.reverse(FooIdentity, "logical_id", uuid.UUID(int=2))
        assert name == "beta"

    async def test_multiple_resolvers(
        self,
        registry: ResolverRegistry,
        foo_resolver: MockFooResolver,
        bar_resolver: MockBarResolver,
    ):
        registry.register(foo_resolver)
        registry.register(bar_resolver)
        await foo_resolver.load(ResolverContext())
        await bar_resolver.load(ResolverContext())

        foo = await registry.resolve(FooIdentity, "alpha")
        bar = await registry.resolve(BarIdentity, "first")
        assert foo.id == 1
        assert bar.id == 10

    def test_context_default_none(self, registry: ResolverRegistry):
        assert registry.current_context is None

    def test_context_manager_scopes_then_restores(
        self, registry: ResolverRegistry, foo_resolver: MockFooResolver
    ):
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
    ):
        registry.register(foo_resolver)
        outer = ResolverContext(enterprise_id=1)
        registry.set_context(outer)
        with pytest.raises(RuntimeError):
            with registry.context(ResolverContext(enterprise_id=2)):
                raise RuntimeError("boom")
        assert registry.current_context is outer

    async def test_context_manager_clears_caches(self, registry: ResolverRegistry):
        # Register a real BaseResolver-style resolver so _clear is exercised.
        from pyvelo.client.protocol import VcoClientProtocol
        from pyvelo.config.resolver import BaseResolver

        class CountedResolver(BaseResolver[object, FooIdentity]):
            identity_type: ClassVar[type[FooIdentity]] = FooIdentity

            def __init__(self) -> None:
                super().__init__(client=cast(VcoClientProtocol, cast(object, None)))
                self.cleared: int = 0

            @override
            def _clear(self) -> None:
                self.cleared += 1
                super()._clear()

            @override
            async def load(self, context: ResolverContext) -> None:
                _ = context
                self._register(
                    "alpha",
                    FooIdentity(name="alpha", id=1, logical_id=uuid.UUID(int=1)),
                    id=1,
                )

        resolver = CountedResolver()
        registry.register(cast(Resolver[object, FooIdentity], cast(object, resolver)))
        await resolver.load(ResolverContext())
        assert await resolver.resolve("alpha")
        with registry.context(ResolverContext(enterprise_id=99)):
            assert resolver.cleared == 1
            with pytest.raises(KeyError, match="Unknown foo"):
                _ = await resolver.resolve("alpha")
