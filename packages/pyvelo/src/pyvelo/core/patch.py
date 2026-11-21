"""The patch format of ``VcoModel.merge_patch``: its one error type.

``PatchError`` is what a patch that cannot be applied raises, with the place in
the patch it failed at.
"""

from typing import override

from .rebase import Path


class PatchError(ValueError):
    """A patch that cannot be applied. ``path`` is where in the patch it failed.

    A pydantic ``ValidationError`` behind it is its ``__cause__``.
    """

    path: Path

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message, path)
        self.message: str = message
        self.path = path

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path.parts else self.message
