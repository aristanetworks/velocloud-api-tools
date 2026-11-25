"""The ``firewall`` module's refs, kept as an untyped dict."""

from typing import Any

from pydantic import RootModel

FirewallRefs = RootModel[dict[str, Any]]
