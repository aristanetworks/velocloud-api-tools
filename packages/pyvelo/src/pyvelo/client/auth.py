from collections.abc import Generator
from typing import override

import httpx


class VcoAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        if len(token.split()) > 1:
            self._auth_header = token
        else:
            self._auth_header = f"Token {token}"

    @override
    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = self._auth_header
        yield request
