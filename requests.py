"""Lightweight fallback for the requests API used in tests.

If the real `requests` package is available, it is imported and exposed.
Otherwise, a minimal shim backed by urllib is provided.
"""
from __future__ import annotations

from typing import Dict, Iterable, Iterator, Optional

import sys
from pathlib import Path
import importlib.machinery
import importlib.util


_current_path = Path(__file__).resolve()

for _path in sys.path[1:]:
    _spec = importlib.machinery.PathFinder.find_spec("requests", [_path])
    if _spec and _spec.origin and Path(_spec.origin).resolve() != _current_path:
        _module = importlib.util.module_from_spec(_spec)
        assert _spec.loader is not None
        _spec.loader.exec_module(_module)
        sys.modules[__name__] = _module
        globals().update(_module.__dict__)
        break
else:
    import urllib.parse
    import urllib.request
    from http.cookies import SimpleCookie

    class RequestException(Exception):
        """Fallback exception matching requests.RequestException."""

    class Response:
        """Minimal response object with the attributes used by the app."""

        def __init__(
            self,
            *,
            status_code: int,
            headers: Dict[str, str],
            content: bytes,
            url: str,
        ) -> None:
            self.status_code = status_code
            self.headers = headers
            self.url = url
            self.content = content
            self.text = content.decode("utf-8", errors="replace")
            self.cookies = self._parse_cookies(headers)

        @staticmethod
        def _parse_cookies(headers: Dict[str, str]) -> Dict[str, str]:
            cookie_header = headers.get("Set-Cookie")
            if not cookie_header:
                return {}
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            return {key: morsel.value for key, morsel in cookie.items()}

        def iter_content(self, chunk_size: int = 1024) -> Iterator[bytes]:
            for idx in range(0, len(self.content), chunk_size):
                yield self.content[idx : idx + chunk_size]

    class Session:
        """Minimal session object implementing get()."""

        def get(
            self,
            url: str,
            *,
            params: Optional[dict] = None,
            stream: bool = False,
            timeout: int = 30,
        ) -> Response:
            try:
                full_url = url
                if params:
                    query = urllib.parse.urlencode(params)
                    delimiter = "&" if "?" in url else "?"
                    full_url = f"{url}{delimiter}{query}"

                request = urllib.request.Request(full_url)
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    content = resp.read() if stream or True else b""
                    headers = {key: value for key, value in resp.headers.items()}
                    return Response(
                        status_code=resp.status,
                        headers=headers,
                        content=content,
                        url=resp.geturl(),
                    )
            except Exception as exc:  # pragma: no cover - mapped to RequestException
                raise RequestException(str(exc)) from exc

    __all__ = ["RequestException", "Response", "Session"]
