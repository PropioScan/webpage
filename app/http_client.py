from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from .errors import UpstreamServiceError


USER_AGENT = "Propioscan/1.5 (+https://propioscan.com)"


class ResilientHTTPClient:
    def __init__(
        self,
        timeout_seconds: float,
        *,
        verify: bool = True,
        client: httpx.Client | None = None,
        attempts: int = 3,
    ) -> None:
        self.attempts = max(1, attempts)
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            verify=verify,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        response = self.request("GET", url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamServiceError(
                f"The official service returned an unreadable response from {response.url.host}."
            ) from exc

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = self.client.request(method, url, **kwargs)
                if (
                    response.status_code in {429, 502, 503, 504}
                    and attempt < self.attempts - 1
                ):
                    time.sleep(0.5 * (2**attempt))
                    continue
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                if attempt < self.attempts - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue
        raise UpstreamServiceError(
            "An official data service is temporarily unavailable. Please try again later."
        ) from last_error


def wfs_params(
    layer: str,
    *,
    cql_filter: str,
    properties: str | None = None,
    srs_name: str = "EPSG:3794",
) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "outputFormat": "application/json",
        "srsName": srs_name,
        "CQL_FILTER": cql_filter,
        "count": 300,
    }
    if properties:
        params["propertyName"] = properties
    return params
