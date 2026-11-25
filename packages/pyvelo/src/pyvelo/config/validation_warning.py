"""ValidationWarning: something a plan or a load reports and leaves as it is."""

from dataclasses import dataclass


@dataclass
class ValidationWarning:
    """Something at ``path`` that a plan or a load reports and leaves as it is.

    ``code`` is machine-readable, ``message`` the text a plan shows.
    """

    path: str
    message: str
    code: str
    severity: str = "warning"
