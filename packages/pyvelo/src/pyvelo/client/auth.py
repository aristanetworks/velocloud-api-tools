from collections.abc import Generator
from typing import override

import httpx

from pyvelo.client.exceptions import VcoCredentialsError

_EMPTY_TOKEN = (
    "The API token is empty. Give the token, or check the file or variable it "
    "is read from."
)
_UNSENDABLE_TOKEN = (
    "The API token has a character that cannot be sent in an HTTP header: a "
    "line break, a tab, another control character or a character that is not "
    "ASCII. The token is not shown here. Check where it comes from, for "
    "example a file with more than one line or text pasted with its formatting."
)


def clean_token(token: str) -> str:
    """Return *token* without the whitespace around it.

    Raises:
        VcoCredentialsError: nothing is left, or the token has a character
            outside printable ASCII (space to ``~``), which no HTTP header can
            carry. The message is fixed text and never holds the token.
    """
    cleaned = token.strip()
    if not cleaned:
        raise VcoCredentialsError(_EMPTY_TOKEN)
    if any(not " " <= char <= "~" for char in cleaned):
        raise VcoCredentialsError(_UNSENDABLE_TOKEN)
    return cleaned


class VcoAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        token = clean_token(token)
        if len(token.split()) > 1:
            self._auth_header: str = token
        else:
            self._auth_header = f"Token {token}"

    @override
    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = self._auth_header
        yield request
