"""The ``WAN`` module's refs, kept as an untyped dict."""

from typing import Any

from pydantic import RootModel

WanRefs = RootModel[dict[str, Any]]
