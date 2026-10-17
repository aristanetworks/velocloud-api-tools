from typing import Any

from pydantic import RootModel

QosRefs = RootModel[dict[str, Any]]
