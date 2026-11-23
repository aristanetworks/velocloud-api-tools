"""Resolver protocol, registry, and cached wrapper for name/ID resolution."""

import contextlib
import copy
import difflib
import uuid
from collections.abc import Callable, Collection, Generator, Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, Self, cast, overload, runtime_checkable

from pyvelo.client.protocol import VcoClientProtocol

# ResolutionError lives in pyvelo.core.identity; re-exported so this import path keeps working.
from pyvelo.core.identity import ResolutionError as ResolutionError  # noqa: PLC0414


@dataclass
class ResolverContext:
    """Scoping context passed to resolvers when loading data."""

    enterprise_id: int | None = None
    edge_id: int | None = None
    configuration_id: int | None = None


@runtime_checkable
class Resolver[KeyT, IdentityT](Protocol):
    """Protocol for resolving human-friendly names to typed identity objects.

    ``KeyT`` is the union of typed key wrappers the resolver accepts via
    ``resolve_key()`` (e.g. ``EdgeName | EdgeId | EdgeLogicalId``).
    ``IdentityT`` is the resolved identity dataclass (e.g. ``EdgeIdentity``).
    """

    identity_type: type[IdentityT]

    async def resolve(self, name: str) -> IdentityT:
        """Name -> typed identity object with all ID fields."""
        ...

    async def resolve_key(self, key: KeyT) -> IdentityT:
        """Key object -> typed identity. Dispatches on key type."""
        ...

    async def reverse_by(self, id_attr: str, value: int | str | uuid.UUID) -> str:
        """ID -> Name. id_attr specifies which field to look up by."""
        ...

    async def load(self, context: ResolverContext) -> None:
        """Batch-load the full lookup table for a given context."""
        ...


class BaseResolver[KeyT, IdentityT]:
    """Common base for resolvers that share the standard lookup structure.

    Subclasses must:
    - Set ``identity_type`` as a class attribute.
    - Declare their accepted key union as ``KeyT`` in the generic parameters.
    - Implement ``resolve_key()`` to dispatch on key type.
    - Implement ``load()`` to fetch entities and call ``_register()``.
    """

    identity_type: ClassVar[type]

    def __init__(self, client: VcoClientProtocol) -> None:
        self._client = client
        self._by_name: dict[str, IdentityT] = {}
        self._reverse: dict[str, dict[int | str | uuid.UUID, str]] = {}

    async def resolve(self, name: str) -> IdentityT:
        _, identity = resolve_name(name, self._by_name, self._entity_label)
        return identity

    async def resolve_key(self, key: KeyT) -> IdentityT:
        raise NotImplementedError

    async def reverse_by(self, id_attr: str, value: int | str | uuid.UUID) -> str:
        reverse_map = self._reverse.get(id_attr)
        if reverse_map is None:
            raise KeyError(f"No reverse index for attribute: {id_attr!r}")
        if value not in reverse_map:
            raise KeyError(f"No {self._entity_label} with {id_attr}={value}")
        return reverse_map[value]

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
        return twin

    def _register(
        self, name: str, identity: IdentityT, **reverse_keys: int | str | uuid.UUID
    ) -> None:
        """Register an identity with forward and reverse indexes."""
        self._by_name[name] = identity
        for attr, value in reverse_keys.items():
            self._reverse.setdefault(attr, {})[value] = name

    def _clear(self) -> None:
        """Clear all lookup tables before a reload."""
        self._by_name.clear()
        self._reverse.clear()

    @property
    def _entity_label(self) -> str:
        import re

        name = self.identity_type.__name__.removesuffix("Identity")
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()


class ResolverRegistry:
    """Registry of resolvers keyed by identity type.

    Resolvers are registered once, then the context is set when the
    configuration pipeline loads data. All resolve/reverse calls
    delegate to the registered resolver for the given identity type.
    """

    def __init__(self) -> None:
        self._resolvers: dict[type, Resolver[Any, Any]] = {}
        self._context: ResolverContext | None = None

    def register[K, T](self, resolver: Resolver[K, T]) -> None:
        """Register a resolver, keyed by its identity_type."""
        self._resolvers[resolver.identity_type] = resolver

    def get_resolver[R: Resolver[Any, Any]](self, resolver_type: type[R]) -> R:
        """Look up a resolver by its concrete class (e.g. EdgeResolver)."""
        for resolver in self._resolvers.values():
            if isinstance(resolver, resolver_type):
                return resolver
        raise LookupError(f"No resolver of type {resolver_type.__name__} registered")

    def unloaded(self) -> "ResolverRegistry":
        """A new registry holding an unloaded copy of each resolver, with no context.

        Each copy is the resolver's own ``unloaded()``. A resolver without that
        method cannot be copied, and raises ``TypeError``.
        """
        twin = ResolverRegistry()
        for resolver in self._resolvers.values():
            make_copy = cast(
                Callable[[], Resolver[Any, Any]] | None,
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
    async def resolve(self, key: Any) -> Any: ...
    @overload
    async def resolve[T](self, key: type[T], name: str) -> T: ...

    async def resolve(self, key: Any, name: str | None = None) -> Any:
        """Resolve a name or key object to a typed identity.

        Supports two calling conventions:
          resolve(SegmentName("Global Segment"))   # key-based dispatch
          resolve(SegmentIdentity, "Global Segment")  # legacy type+name
        """
        if isinstance(key, type):
            # Legacy API: resolve(IdentityType, name)
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
    def resolve_sync(self, key: Any) -> Any: ...
    @overload
    def resolve_sync[T](self, key: type[T], name: str) -> T: ...

    def resolve_sync(self, key: Any, name: str | None = None) -> Any:
        """Synchronously resolve a name or key to a typed identity object.

        Only works after the resolver has been loaded (all built-in
        resolvers do pure dict lookups, so no event loop is needed).
        """
        if isinstance(key, type):
            resolver = self._resolvers.get(key)
            if resolver is None:
                raise LookupError(f"No resolver registered for {key.__name__}")
            if name is None:
                raise TypeError("name is required when resolving by identity type")
            coro = resolver.resolve(name)
        else:
            identity_type = type(key).identity_type
            resolver = self._resolvers.get(identity_type)
            if resolver is None:
                raise LookupError(
                    f"No resolver registered for {identity_type.__name__}"
                )
            coro = resolver.resolve_key(key)

        try:
            coro.send(None)
        except StopIteration as e:
            return e.value
        finally:
            coro.close()

        type_name = key.__name__ if isinstance(key, type) else type(key).__name__
        raise RuntimeError(
            f"Resolver for {type_name} suspended unexpectedly "
            f"(resolve() must not await)"
        )

    def has_id_sync(self, identity_type: type, id_attr: str, value: object) -> bool:
        """True if a loaded identity of *identity_type* has ``id_attr == value``.

        False when no resolver is registered for the type or it does not
        expose ``identities()``.
        """
        identities = cast(
            Callable[[], Iterable[object]] | None,
            getattr(self._resolvers.get(identity_type), "identities", None),
        )
        if identities is None:
            return False
        return any(getattr(i, id_attr) == value for i in identities())

    async def reverse(
        self, identity_type: type, id_attr: str, value: int | str | uuid.UUID
    ) -> str:
        """Reverse-resolve an ID to a name."""
        resolver = self._resolvers.get(identity_type)
        if resolver is None:
            raise LookupError(f"No resolver registered for {identity_type.__name__}")
        return await resolver.reverse_by(id_attr, value)


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
