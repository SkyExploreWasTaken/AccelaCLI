"""Headless HTTP transport for Hubcap API requests."""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from utils.settings import get_settings


def execute_hubcap_request(
    session: requests.Session,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    stream: bool = False,
) -> requests.Response:
    """Execute a Hubcap request using the configured headless transport."""
    settings = get_settings()
    request_kwargs: Dict[str, Any] = {
        "headers": headers,
        "params": params,
        "timeout": timeout,
        "stream": stream,
    }
    proxy = settings.value("hubcap_proxy", "", type=str).strip()
    if proxy:
        request_kwargs["proxies"] = {"http": proxy, "https": proxy}
    response = session.request(method, url, **request_kwargs)
    response.raise_for_status()
    return response
