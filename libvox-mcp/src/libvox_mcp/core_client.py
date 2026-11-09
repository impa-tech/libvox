"""HTTP client for `/libvox-core`, which implements the tools.

This module is the whole of the MCP layer's outbound surface. It never touches
an ILS, the Discovery Index, or a credential vault — it forwards a tenant-scoped
request to Core and returns what Core says.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2

from .models import CheckAvailabilityResult, SearchCatalogResult

logger = logging.getLogger(__name__)


class CoreUnavailableError(RuntimeError):
    """Core could not be reached, or answered with something unusable."""


class CoreClient:
    """Thin async wrapper over Core's internal API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        service_token: str | None = None,
    ) -> None:
        headers = {"accept": "application/json"}
        if service_token:
            # Identifies this MCP instance to Core. Not a tenant credential.
            headers["x-libvox-service-token"] = service_token
        self._client = httpx2.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx2.TimeoutException as exc:
            raise CoreUnavailableError(
                "The library service did not respond in time."
            ) from exc
        except httpx2.HTTPStatusError as exc:
            # Log the detail; hand the model something it can act on.
            logger.warning(
                "core returned %s for %s", exc.response.status_code, path
            )
            raise CoreUnavailableError(
                "The library service rejected the request."
            ) from exc
        except httpx2.HTTPError as exc:
            raise CoreUnavailableError(
                "The library service is unreachable."
            ) from exc

        if not isinstance(body, dict):
            raise CoreUnavailableError(
                "The library service returned an unexpected response."
            )
        return body

    async def introspect_session(self, token: str) -> dict[str, Any] | None:
        """Resolve a session credential to its tenant, or None if invalid.

        Core is the only component that can answer this — it owns the Tenant
        DB and minted the credential in the first place.
        """
        try:
            response = await self._client.post(
                "/internal/v1/sessions/introspect", json={"token": token}
            )
        except httpx2.HTTPError:
            logger.warning("core unreachable during token introspection")
            return None

        if response.status_code in (401, 403, 404):
            return None
        if response.status_code >= 400:
            logger.warning(
                "core returned %s during token introspection", response.status_code
            )
            return None

        body = response.json()
        if not isinstance(body, dict) or not body.get("active"):
            return None
        return body

    async def search_catalog(
        self,
        *,
        tenant_id: str,
        query: str,
        author: str | None,
        title: str | None,
        year_from: int | None,
        year_to: int | None,
        limit: int,
    ) -> SearchCatalogResult:
        body = await self._post(
            "/internal/v1/search_catalog",
            {
                "tenant_id": tenant_id,
                "query": query,
                "author": author,
                "title": title,
                "year_from": year_from,
                "year_to": year_to,
                "limit": limit,
            },
        )
        return SearchCatalogResult.model_validate(body)

    async def check_availability(
        self, *, tenant_id: str, biblio_ids: list[int]
    ) -> CheckAvailabilityResult:
        body = await self._post(
            "/internal/v1/check_availability",
            {"tenant_id": tenant_id, "biblio_ids": biblio_ids},
        )
        return CheckAvailabilityResult.model_validate(body)
