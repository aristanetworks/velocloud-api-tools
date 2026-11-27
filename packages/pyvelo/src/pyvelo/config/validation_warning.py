"""ValidationWarning: something a plan or a load reports and leaves as it is."""

from dataclasses import dataclass


@dataclass
class ValidationWarning:
    """Something at ``path`` that a plan or a load reports and leaves as it is.

    ``code`` is machine-readable, ``message`` the text a plan shows.
    """

    path: str
    """Where the thing is.

    In a warning that names its ``module``, the path within that module's data
    as pyvelo shows it (a ``Path``): fields by attribute name, items by what
    names them.
    """
    message: str
    code: str
    severity: str = "warning"
    module: str | None = None
    """The module the warning is about, by its python name, where it is about one."""
    segment: str | None = None
    """The name of the segment the warning is about, where it is about one."""
