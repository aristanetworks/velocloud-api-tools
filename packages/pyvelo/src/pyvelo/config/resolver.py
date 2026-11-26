"""Resolver protocol, registry, and cached wrapper for name/ID resolution."""

import contextlib
import copy
import difflib
import uuid
from collections.abc import Callable, Collection, Generator
from dataclasses import dataclass, fields, is_dataclass
from typing import (
    Any,
    ClassVar,
    Protocol,
    Self,
    cast,
    final,
    overload,
    runtime_checkable,
)

from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.core.identity import (
    IdentityKey,
    ResolverNotLoadedError,
    entity_label,
    resolution_error,
)

# ResolutionError lives in pyvelo.core.identity; re-exported so this import path keeps
# working.
from pyvelo.core.identity import ResolutionError as ResolutionError  # noqa: PLC0414


@dataclass
class ResolverContext:
    """Scoping context passed to resolvers when loading data."""

    enterprise_id: int | None = None
    edge_id: int | None = None
    configuration_id: int | None = None


@runtime_checkable
class Resolver[IdentityT](Protocol):
    """Protocol for resolving human-friendly names to typed identity objects.

    ``IdentityT`` is the resolved identity dataclass (e.g. ``EdgeIdentity``).
    The keys a resolver accepts are the :class:`IdentityKey` classes of that
    identity (``EdgeName``, ``EdgeId``, ...).

    ``lookup`` and ``lookup_key`` answer from the loaded tables, without an
    event loop; ``resolve`` and ``resolve_key`` are their awaitable forms.
    """

    identity_type: type[IdentityT]
    requires_load: ClassVar[bool]
    """A resolver whose answers would be wrong before a load sets ``requires_load``."""

    @property
    def loaded(self) -> bool:
        """Whether ``load()`` has filled the tables."""
        ...

    def lookup(self, name: str) -> IdentityT:
        """Name -> typed identity object with all ID fields, from the loaded tables."""
        ...

    def lookup_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        """Key object -> typed identity, from the loaded tables.

        Dispatches on key type.
        """
        ...

    async def resolve(self, name: str) -> IdentityT:
        """Name -> typed identity object with all ID fields."""
        ...

    async def resolve_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        """Key object -> typed identity. Dispatches on key type."""
        ...

    async def load(self, context: ResolverContext) -> None:
        """Batch-load the full lookup table for a given context."""
        ...

    def identities(self) -> Collection[IdentityT]:
        """The identities loaded so far."""
        ...


class BaseResolver[IdentityT]:
    """Common base for resolvers that share the standard lookup structure.

    Subclasses must:
    - Set ``identity_type`` as a class attribute.
    - Implement ``lookup_key()`` to dispatch on key type, answering an ID key
      with ``_by_key()``.
    - Implement ``_load()`` to fetch entities and call ``_register()``.

    ``resolve()``, ``resolve_key()`` and ``load()`` are final. The first two
    return what ``lookup()`` and ``lookup_key()`` return, so a subclass
    overrides the lookups and the awaitable forms cannot disagree with them.
    ``load()`` awaits ``_load()`` and then sets ``loaded``, so every resolver
    says whether it is loaded; a ``_load()`` that raises leaves ``loaded`` as
    it was.

    A resolver whose answers would be wrong before a load (it holds every
    identity of an enterprise, not those a name asks for) sets
    ``requires_load``: until a ``load()`` has finished, ``lookup``,
    ``lookup_key`` and ``resolve`` raise ``ResolverNotLoadedError``.
    ``loaded`` is read-only; ``unloaded()`` and ``_clear()`` set it back to
    ``False``.
    """

    identity_type: type[IdentityT]  # pyright: ignore[reportUninitializedInstanceVariable]
    requires_load: ClassVar[bool] = False

    def __init__(self, client: VcoClientProtocol) -> None:
        """An empty resolver that loads its tables through *client*."""
        self._client: VcoClientProtocol = client
        self._loaded: bool = False
        self._by_name: dict[str, IdentityT] = {}
        self._reverse: dict[str, dict[int | str | uuid.UUID, IdentityT]] = {}

    def lookup(self, name: str) -> IdentityT:
        """The identity loaded under *name*, matched exactly or ignoring case."""
        self._require_loaded()
        _, identity = resolve_name(name, self._by_name, self.identity_type)
        return identity

    def lookup_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        """The identity *key* names; a subclass dispatches on the key's type."""
        del key
        raise NotImplementedError

    @final
    async def resolve(self, name: str) -> IdentityT:
        """The awaitable form of ``lookup``."""
        return self.lookup(name)

    @final
    async def resolve_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        """The awaitable form of ``lookup_key``."""
        return self.lookup_key(key)

    @property
    def loaded(self) -> bool:
        """Whether ``load()`` has filled the tables."""
        return self._loaded

    @final
    async def load(self, context: ResolverContext) -> None:
        """Run ``_load`` for *context*, then set ``loaded``.

        A ``_load`` that raises leaves ``loaded`` as it was.
        """
        await self._load(context)
        self._loaded = True

    async def _load(self, context: ResolverContext) -> None:
        """Fetch the entities of *context* and register them; a subclass does this."""
        del context
        raise NotImplementedError

    def identities(self) -> Collection[IdentityT]:
        """The identities loaded so far."""
        return self._by_name.values()

    def unloaded(self) -> Self:
        """A copy of this resolver, on the same client, with nothing loaded.

        Loading the copy leaves this resolver as it was, and the other way
        round. A subclass that keeps other loaded data overrides this to give
        the copy its own, empty.
        """
        twin = copy.copy(self)
        twin._by_name = {}
        twin._reverse = {}
        twin._loaded = False
        return twin

    def _register(
        self, name: str, identity: IdentityT, **reverse_keys: int | str | uuid.UUID
    ) -> None:
        """Register *identity* under *name* and under each of *reverse_keys*.

        Each reverse key is a field of the identity type; the identity is kept
        under its value, so an ID finds it also when a later identity of the
        same name took its place under *name*.
        """
        for attr in reverse_keys:
            self._check_field(attr)
        self._by_name[name] = identity
        for attr, value in reverse_keys.items():
            self._reverse.setdefault(attr, {})[value] = identity

    def _by_key(self, attribute: str, value: object) -> IdentityT:
        """The identity registered under ``attribute=value``.

        Raises ``ResolutionError`` when no loaded identity has the value, so an
        ID that is not there is told from a name that is not, and
        ``TypeError`` when *attribute* is no field of the identity type.
        """
        self._check_field(attribute)
        self._require_loaded()
        identity = self._reverse.get(attribute, {}).get(cast("Any", value))
        if identity is None:
            raise ResolutionError(
                name=str(value),
                identity_type=self.identity_type,
                candidates=[],
                message=f"No {self._entity_label} has {attribute} {value}.",
            )
        return identity

    def _check_field(self, attribute: str) -> None:
        """Raise ``TypeError`` when *attribute* is no field of the identity type.

        Only a dataclass identity type is checked.
        """
        if not is_dataclass(self.identity_type):
            return
        names = [field.name for field in fields(self.identity_type)]
        if attribute in names:
            return
        raise TypeError(
            f"{type(self).__name__}: {self.identity_type.__name__} has no field "
            f"{attribute!r} (it has {', '.join(names)}). This is a mistake in the "
            "resolver, not an unknown ID: register and look up identities under "
            "the names of the identity's fields."
        )

    def _clear(self) -> None:
        """Clear all lookup tables before a reload."""
        self._by_name.clear()
        self._reverse.clear()
        self._loaded = False

    def _require_loaded(self) -> None:
        """Raise ``ResolverNotLoadedError`` if this resolver is not loaded.

        That is when it needs a load and has none.
        """
        if self.requires_load and not self.loaded:
            raise ResolverNotLoadedError(self.identity_type)

    @property
    def _entity_label(self) -> str:
        return entity_label(self.identity_type)


class ResolverRegistry:
    """Registry of resolvers keyed by identity type.

    Resolvers are registered once, then the context is set when the
    configuration pipeline loads data. All lookup and resolve calls
    delegate to the registered resolver for the given identity type.
    """

    def __init__(self) -> None:
        """A registry with no resolvers and no context."""
        self._resolvers: dict[type, Resolver[Any]] = {}
        self._context: ResolverContext | None = None

    def register[T](self, resolver: Resolver[T]) -> None:
        """Register a resolver, keyed by its identity_type."""
        self._resolvers[resolver.identity_type] = resolver

    def get_resolver[R: Resolver[Any]](self, resolver_type: type[R]) -> R:
        """Look up a resolver by its concrete class (e.g. EdgeResolver)."""
        for resolver in self._resolvers.values():
            if isinstance(resolver, resolver_type):
                return resolver
        raise LookupError(f"No resolver of type {resolver_type.__name__} registered")

    def resolver_for[T](self, identity_type: type[T]) -> Resolver[T] | None:
        """The resolver registered for *identity_type*, or None when there is none."""
        return self._resolvers.get(identity_type)

    def unloaded(self) -> "ResolverRegistry":
        """A new registry holding an unloaded copy of each resolver, with no context.

        Each copy is the resolver's own ``unloaded()``. A resolver without that
        method cannot be copied, and raises ``TypeError``.
        """
        twin = ResolverRegistry()
        for resolver in self._resolvers.values():
            make_copy = cast(
                "Callable[[], Resolver[Any]] | None",
                getattr(resolver, "unloaded", None),
            )
            if make_copy is None:
                raise TypeError(
                    f"{type(resolver).__name__} has no unloaded() method, so a state "
                    "cannot be given a copy of it; add one that returns a new "
                    "resolver of the class with nothing loaded"
                )
            twin.register(make_copy())
        return twin

    def set_context(self, context: ResolverContext) -> None:
        """Set the scoping context for all resolvers.

        Replaces any previously-set context. Resolver caches are not
        invalidated automatically; callers that mix contexts within a single
        registry must use :meth:`context` instead.
        """
        self._context = context

    @contextlib.contextmanager
    def context(self, ctx: ResolverContext) -> Generator[ResolverContext]:
        """Scope the registry to a specific :class:`ResolverContext`.

        Caches on each registered resolver that share state across contexts
        (the BaseResolver ``_by_name``/``_reverse`` dicts) are cleared on
        entry, and the previous context is restored on exit. Use this when a
        single :class:`ResolverRegistry` instance is shared across multiple
        resources whose resolution scope differs (e.g. a reconciler iterating
        over edges or profiles).
        """
        prev = self._context
        self._context = ctx
        for r in self._resolvers.values():
            clear = getattr(r, "_clear", None)
            if callable(clear):
                clear()
        try:
            yield ctx
        finally:
            self._context = prev

    @property
    def current_context(self) -> ResolverContext | None:
        """Return the currently-active resolver context (if any)."""
        return self._context

    @overload
    async def resolve[T](self, key: IdentityKey[T]) -> T: ...
    @overload
    async def resolve[T](self, key: type[T], name: str) -> T: ...

    async def resolve(self, key: Any, name: str | None = None) -> Any:
        """Resolve a name or key object to a typed identity.

        Supports two calling conventions:
          resolve(SegmentName("Global Segment"))   # key-based dispatch
          resolve(SegmentIdentity, "Global Segment")  # identity type + name
        """
        if isinstance(key, type):
            # Called as resolve(IdentityType, name).
            resolver = self._resolvers.get(key)
            if resolver is None:
                raise LookupError(f"No resolver registered for {key.__name__}")
            if name is None:
                raise TypeError("name is required when resolving by identity type")
            return await resolver.resolve(name)
        # Key-based API: resolve(SegmentName("value"))
        identity_type = type(key).identity_type
        resolver = self._resolvers.get(identity_type)
        if resolver is None:
            raise LookupError(f"No resolver registered for {identity_type.__name__}")
        return await resolver.resolve_key(key)

    @overload
    def lookup[T](self, key: IdentityKey[T]) -> T: ...
    @overload
    def lookup[T](self, key: type[T], name: str) -> T: ...

    def lookup(self, key: Any, name: str | None = None) -> Any:
        """Look up a name or key in the loaded tables, and return its identity.

        Supports the two calling conventions of ``resolve``. It needs no event
        loop: every resolver answers from tables ``load()`` filled. Raises
        ``LookupError`` when no resolver is registered for the identity type,
        ``ResolverNotLoadedError`` when the resolver needs a load and has none,
        and ``ResolutionError`` when the table holds no such name or ID.
        """
        if isinstance(key, type):
            resolver = self._resolvers.get(key)
            if resolver is None:
                raise LookupError(f"No resolver registered for {key.__name__}")
            if name is None:
                raise TypeError("name is required when looking up by identity type")
            return resolver.lookup(name)
        identity_type = type(key).identity_type
        resolver = self._resolvers.get(identity_type)
        if resolver is None:
            raise LookupError(f"No resolver registered for {identity_type.__name__}")
        return resolver.lookup_key(key)

    def lookup_by[T](
        self,
        identity_type: type[T],
        accessor: Callable[[T], object],
        value: object,
    ) -> T:
        """The first loaded identity of *identity_type* whose accessor gives *value*.

        It is the first for which ``accessor(identity) == value``.

        The scan covers ``identities()``, in its order, so it finds an identity
        that another one of the same name hides from the name lookups (an Edge
        and a hub cluster that share a name). Raises ``ResolverNotLoadedError``
        when no resolver is registered for the type, or the resolver requires a
        load and has none, and ``ResolutionError`` when no loaded identity has
        the value.
        """
        resolver = self.resolver_for(identity_type)
        if resolver is None or (resolver.requires_load and not resolver.loaded):
            raise ResolverNotLoadedError(identity_type)
        for identity in resolver.identities():
            if accessor(identity) == value:
                return identity
        raise ResolutionError(
            name=str(value),
            identity_type=identity_type,
            candidates=[],
            message=f"No {entity_label(identity_type)} has the value {value!r}.",
        )

    @overload
    def find[T](self, key: IdentityKey[T]) -> T | None: ...
    @overload
    def find[T](self, key: type[T], name: str) -> T | None: ...

    def find(self, key: Any, name: str | None = None) -> Any:
        """The identity ``lookup`` returns, or ``None`` where none has the name or ID.

        Supports the two calling conventions of ``lookup``:
          find(SegmentName("Global Segment"))   # key-based dispatch
          find(SegmentIdentity, "Global Segment")  # identity type + name

        ``None`` says that no loaded identity has the name or the ID, never
        that the table is not loaded or that several identities share the
        name: ``LookupError`` (no resolver), ``ResolverNotLoadedError`` and
        the ``ResolutionError`` for a name some loaded identity has are raised
        as ``lookup`` raises them. The name for an ID is the ``name`` of
        ``registry.find(NetworkServiceLogicalId(u))``, when it is not ``None``.
        """
        try:
            return self.lookup(key) if name is None else self.lookup(key, name)
        except ResolverNotLoadedError:
            raise
        except ResolutionError:
            if isinstance(key, type):
                identity_type: type = key
                text: object = name
            else:
                identity_type = type(key).identity_type
                text = getattr(key, "value", None)
            if isinstance(text, str) and self._has_name(identity_type, text):
                raise
            return None

    def identities[T](self, identity_type: type[T]) -> Collection[T]:
        """The loaded identities of *identity_type*.

        Empty when no resolver is registered for the type, or its resolver
        requires a load and has none. It never raises.
        """
        resolver = self.resolver_for(identity_type)
        if resolver is None or (resolver.requires_load and not resolver.loaded):
            return ()
        return resolver.identities()

    def _has_name(self, identity_type: type, name: str) -> bool:
        """Whether a loaded identity of *identity_type* has *name*, ignoring case."""
        lowered = name.lower()
        return any(
            isinstance(found := getattr(identity, "name", None), str)
            and found.lower() == lowered
            for identity in self.identities(identity_type)
        )


def resolve_name[T](
    name: str,
    by_name: dict[str, T],
    identity_type: type,
    *,
    fuzzy: bool = False,
) -> tuple[str, T]:
    """Resolve a name of an *identity_type* on an exact or case-insensitive match.

    Returns ``(canonical_name, value)`` on success.

    Resolution order:
    1. Exact match
    2. Case-insensitive match
    3. Anything else raises ``ResolutionError`` whose ``candidates`` hold the
       close matches, else the names of *by_name* its message lists. With
       ``fuzzy=True`` a single close match is accepted instead of raising.
    """
    # 1. Exact match
    if name in by_name:
        return name, by_name[name]

    # 2. Case-insensitive match
    lower_map: dict[str, str] = {k.lower(): k for k in by_name}
    canonical = lower_map.get(name.lower())
    if canonical is not None:
        return canonical, by_name[canonical]

    # 3. Opt-in fuzzy match: accept only a single close candidate
    if fuzzy:
        suggestions = difflib.get_close_matches(
            name.lower(), lower_map.keys(), n=5, cutoff=0.6
        )
        if len(suggestions) == 1:
            matched = lower_map[suggestions[0]]
            return matched, by_name[matched]

    # No match, or a near miss that was not opted into: raise with candidates
    raise resolution_error(identity_type, name, by_name.keys())
