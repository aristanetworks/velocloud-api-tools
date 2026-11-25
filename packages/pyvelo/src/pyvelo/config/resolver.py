"""Resolver protocol, registry, and cached wrapper for name/ID resolution."""

import contextlib
import copy
import difflib
import uuid
from collections.abc import Callable, Collection, Generator, Iterable
from dataclasses import dataclass
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
from pyvelo.core.identity import IdentityKey, ResolverNotLoadedError, entity_label

# ResolutionError lives in pyvelo.core.identity; re-exported so this import path keeps working.
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

    def lookup(self, name: str) -> IdentityT:
        """Name -> typed identity object with all ID fields, from the loaded tables."""
        ...

    def lookup_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        """Key object -> typed identity, from the loaded tables. Dispatches on key type."""
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
    - Implement ``load()`` to fetch entities and call ``_register()``.

    ``resolve()`` and ``resolve_key()`` are final: they return what ``lookup()``
    and ``lookup_key()`` return, so a subclass overrides the lookups and the
    awaitable forms cannot disagree with them.

    A resolver whose answers would be wrong before a load (it holds every
    identity of an enterprise, not those a name asks for) sets
    ``requires_load``: until its ``load()`` sets ``loaded``, ``lookup``,
    ``lookup_key`` and ``resolve`` raise ``ResolverNotLoadedError``.
    ``unloaded()`` and ``_clear()`` set ``loaded`` back to ``False``.
    """

    identity_type: type[IdentityT]  # pyright: ignore[reportUninitializedInstanceVariable]
    requires_load: ClassVar[bool] = False

    def __init__(self, client: VcoClientProtocol) -> None:
        self._client = client
        self.loaded: bool = False
        self._by_name: dict[str, IdentityT] = {}
        self._reverse: dict[str, dict[int | str | uuid.UUID, str]] = {}

    def lookup(self, name: str) -> IdentityT:
        self._require_loaded()
        _, identity = resolve_name(name, self._by_name, self._entity_label)
        return identity

    def lookup_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        raise NotImplementedError

    @final
    async def resolve(self, name: str) -> IdentityT:
        return self.lookup(name)

    @final
    async def resolve_key(self, key: IdentityKey[IdentityT]) -> IdentityT:
        return self.lookup_key(key)

    async def load(self, context: ResolverContext) -> None:
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
        twin.loaded = False
        return twin

    def _register(
        self, name: str, identity: IdentityT, **reverse_keys: int | str | uuid.UUID
    ) -> None:
        """Register an identity with forward and reverse indexes."""
        self._by_name[name] = identity
        for attr, value in reverse_keys.items():
            self._reverse.setdefault(attr, {})[value] = name

    def _by_key(self, attribute: str, value: object) -> IdentityT:
        """The identity registered under ``attribute=value``.

        Raises ``ResolutionError`` when no loaded identity has the value, so an
        ID that is not there is told from a name that is not.
        """
        self._require_loaded()
        name = self._reverse.get(attribute, {}).get(cast(Any, value))
        if name is None:
            raise ResolutionError(
                name=str(value),
                identity_type=self.identity_type,
                candidates=[],
                message=f"No {self._entity_label} has {attribute} {value}.",
            )
        return self._by_name[name]

    def _clear(self) -> None:
        """Clear all lookup tables before a reload."""
        self._by_name.clear()
        self._reverse.clear()
        self.loaded = False

    def _require_loaded(self) -> None:
        """Raise ``ResolverNotLoadedError`` when this resolver needs a load and has none."""
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
                Callable[[], Resolver[Any]] | None,
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
            # resolve(IdentityType, name)
            resolver = self._resolvers.get(key)
            if resolver is None:
                raise LookupError(f"No resolver registered for {key.__name__}")
            if name is None:
                raise TypeError("name is required when resolving by identity type")
            return await resolver.resolve(name)
        else:
            # Key-based API: resolve(SegmentName("value"))
            identity_type = type(key).identity_type
            resolver = self._resolvers.get(identity_type)
            if resolver is None:
                raise LookupError(
                    f"No resolver registered for {identity_type.__name__}"
                )
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
        """The first loaded identity of *identity_type* for which ``accessor(identity) == value``.

        The scan covers ``identities()``, in its order, so it finds an identity
        that another one of the same name hides from the name lookups (an Edge
        and a hub cluster that share a name). Raises ``ResolverNotLoadedError``
        when no resolver is registered for the type, or the resolver requires a
        load and has none, and ``ResolutionError`` when no loaded identity has
        the value.
        """
        resolver = self.resolver_for(identity_type)
        if resolver is None or (
            getattr(resolver, "requires_load", False)
            and not getattr(resolver, "loaded", False)
        ):
            raise ResolverNotLoadedError(identity_type)
        for identity in resolver.identities():
            if accessor(identity) == value:
                return identity
        raise ResolutionError(
            name=str(value), identity_type=identity_type, candidates=[]
        )


def resolve_name[T](
    name: str,
    by_name: dict[str, T],
    entity_label: str,
    *,
    fuzzy: bool = False,
    n: int = 5,
    cutoff: float = 0.6,
) -> tuple[str, T]:
    """Resolve a name on an exact or case-insensitive match.

    Returns ``(canonical_name, value)`` on success.

    Resolution order:
    1. Exact match
    2. Case-insensitive match
    3. Anything else raises ``ResolutionError`` whose ``candidates`` hold the
       close matches. With ``fuzzy=True`` a single close match is accepted
       instead of raising.
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
            name.lower(), lower_map.keys(), n=n, cutoff=cutoff
        )
        if len(suggestions) == 1:
            matched = lower_map[suggestions[0]]
            return matched, by_name[matched]

    # No match, or a near miss that was not opted into: raise with candidates
    raise resolution_error(entity_label, name, by_name.keys(), n=n, cutoff=cutoff)


def resolution_error(
    entity_label: str,
    name: str,
    available: Iterable[str],
    *,
    n: int = 5,
    cutoff: float = 0.6,
) -> ResolutionError:
    """Build a ResolutionError with fuzzy 'did you mean?' suggestions.

    Uses case-insensitive similarity matching via ``difflib.get_close_matches``
    to suggest the most likely intended names. Returns a ``ResolutionError``
    (subclass of ``KeyError`` for backwards compatibility).
    """
    available_list = sorted(available)

    # Case-insensitive matching: build lowercase -> original map
    lower_map = {k.lower(): k for k in available_list}
    suggestions = difflib.get_close_matches(
        name.lower(), lower_map.keys(), n=n, cutoff=cutoff
    )
    original_suggestions = [lower_map[s] for s in suggestions]

    parts = [f"Unknown {entity_label}: {name!r}."]
    if original_suggestions:
        formatted = "\n".join(f"  - {s}" for s in original_suggestions)
        parts.append(f"Did you mean:\n{formatted}")
    else:
        if len(available_list) > 20:
            shown = ", ".join(available_list[:20])
            parts.append(
                f"Available ({len(available_list)} total, showing first 20): {shown}"
            )
        else:
            parts.append(f"Available: {', '.join(available_list)}")
    return ResolutionError(
        name=name,
        identity_type=entity_label,
        candidates=original_suggestions,
        message="\n".join(parts),
    )
