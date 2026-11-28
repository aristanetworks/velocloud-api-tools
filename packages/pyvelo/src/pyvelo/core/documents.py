"""The refusal of a document in attribute names.

A document in attribute names is what pyvelo shows of a model: each field by
its python attribute, each secret as a mask. ``NamedDocumentError`` is what
reading one back raises where it cannot be read.

This module imports nothing of the package but ``rebase``, so every module
that reads such a document can raise the one error.
"""

from typing import Literal, override

from .rebase import Path

type NamedDocumentKind = Literal["vco_key", "masked_secret", "invalid_value"]
"""Which sort of refusal a ``NamedDocumentError`` is."""


class NamedDocumentError(ValueError):
    """A document in attribute names that cannot be read.

    ``path`` is where, in its own names: a field is its attribute name, an
    entry of a map its key, and an item ``[#n]``, its position in the
    document. ``kind`` says which sort of refusal it is; the document:

    - ``vco_key``: names a field by a key of the VCO's form, where a document
      in attribute names has the attribute.
    - ``masked_secret``: holds the mask of a secret where no stored value is
      known to keep in its place.
    - ``invalid_value``: holds a value that validation refuses.
    """

    path: Path
    kind: NamedDocumentKind
    message: str

    def __init__(self, message: str, path: Path, kind: NamedDocumentKind) -> None:
        """*message* says what is wrong, at *path* in the document, of kind *kind*."""
        super().__init__(message, path, kind)
        self.message = message
        self.path = path
        self.kind = kind

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path.parts else self.message
