from typing import Any

from pydantic import RootModel

FirewallRefs = RootModel[dict[str, Any]]
