"""ValidationWarning: something a plan or a load reports and leaves as it is."""

from dataclasses import dataclass

from pyvelo.core.rebase import Path


@dataclass
class ValidationWarning:
    """Something at ``path`` that a plan or a load reports and leaves as it is.

    ``code`` is machine-readable, ``message`` the text a plan shows.
    """

    path: Path | str
    """Where the thing is.

    A ``Path``, as pyvelo shows one, with no module before it: fields by
    attribute name, items by what names them. In a warning that names its
    ``module`` it is a place within that module's data; a ref row that
    nothing in the data names is at its ref name under ``refs``
    (``refs."deviceSettings:css:provider"``), and a warning about the whole
    module has the empty path.

    A validator's finding, at a load or at a save, is the one exception: its
    path is the text the validator wrote, and it names no ``module``.
    """
    message: str
    code: str
    severity: str = "warning"
    module: str | None = None
    """The module the warning is about, by its python name, where it is about one."""
    segment: str | None = None
    """The name of the segment the warning is about, where it is about one.

    For a ref row that nothing in the data names it is the one place that
    says which segment the row is of. None for a warning a module's plan
    gives at a field: there the path names the segment.
    """
