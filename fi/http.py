"""Minimaler HTTP-Client ohne externe Bibliotheken."""
from __future__ import annotations

import urllib.error
import urllib.request

from fi import config


class NotFound(Exception):
    """Die Ressource existiert nicht (HTTP 404)."""


def get_bytes(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise


def get_json_with_headers(url: str, headers: dict | None = None, timeout: int = 30):
    """Wie get_bytes, liefert aber zusätzlich die Antwort-Header (z.B. Restguthaben einer API)."""
    import json
    request = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read()), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise
