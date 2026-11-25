"""The ``QOS`` module's refs, kept as an untyped dict."""

from typing import Any

from pydantic import RootModel

QosRefs = RootModel[dict[str, Any]]
