from typing import Any

from pydantic import RootModel

WanRefs = RootModel[dict[str, Any]]
