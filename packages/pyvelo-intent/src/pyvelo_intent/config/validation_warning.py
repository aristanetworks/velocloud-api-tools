"""ValidationWarning: something a plan or a load reports and leaves as it is."""

from dataclasses import dataclass


@dataclass
class ValidationWarning:
    path: str
    message: str
    code: str
    severity: str = "warning"
