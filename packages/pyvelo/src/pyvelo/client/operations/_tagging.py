"""Domain tagging for VCO API operations.

Tags are stored as a ``set[str]`` on the function attribute
``__pyvelo_domain_tags__`` and read by pyvelo-mcp during tool discovery.

"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

# ---------------------------------------------------------------------------
# Domain tag constants
# ---------------------------------------------------------------------------
ADMIN: str = "admin"
COMMON: str = "common"
CONFIG: str = "config"
EDGE: str = "edge"
EVENTS: str = "events"
PM_QOE: str = "pm_qoe"
PM_FLOW: str = "pm_flow"
PM_HEALTH: str = "pm_health"
SECURITY: str = "security"
SYSTEM: str = "system"


class _Tagged(Protocol):
    __pyvelo_domain_tags__: set[str]


def domain_tag[F: Callable[..., object]](*tags: str) -> Callable[[F], F]:
    """Mark a method with one or more domain tags.

    Usage::

        @domain_tag("pm_qoe")
        async def get_edge_link_metrics(self, request):
            ...

        @domain_tag("security", "pm_health")
        async def get_edge_firewall_idps_metrics(self, request):
            ...
    """

    def decorator(fn: F) -> F:
        cast(_Tagged, fn).__pyvelo_domain_tags__ = set(tags)
        return fn

    return decorator


def get_domain_tags(fn: Callable[..., object]) -> set[str] | None:
    """Read domain tags from a decorated function, or None if untagged."""
    return cast(set[str] | None, getattr(fn, "__pyvelo_domain_tags__", None))
