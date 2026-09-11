"""REST client for api.prod.biedronka.cloud v7."""

from __future__ import annotations

import json as json_lib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .auth import jwt_payload, refresh_tokens
from .const import ACCEPT_LANGUAGE, API_BASE, API_USER_AGENT
from .exceptions import BiedronkaAuthError, BiedronkaCannotConnect, BiedronkaError

_LOGGER = logging.getLogger(__name__)

SaveTokens = Callable[[dict[str, str]], Awaitable[None]]


def _loads_json(text: str) -> Any:
    """Parse a body as JSON even when the server omits application/json."""
    if not text:
        return None
    try:
        return json_lib.loads(text)
    except json_lib.JSONDecodeError:
        return text


class BiedronkaApi:
    """Thin authenticated JSON client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str,
        save_tokens: SaveTokens | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._save_tokens = save_tokens

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def ensure_fresh_token(self) -> None:
        payload = jwt_payload(self._access_token)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            if exp - 60 > time.time():
                return
        await self._refresh()

    async def _refresh(self) -> None:
        tokens = await refresh_tokens(self._session, self._refresh_token)
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens["refresh_token"]
        if self._save_tokens:
            await self._save_tokens(tokens)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        force_json: bool = False,
    ) -> Any:
        await self.ensure_fresh_token()
        return await self._request_once(
            method,
            path,
            params=params,
            json=json,
            extra_headers=headers,
            force_json=force_json,
            retry=True,
        )

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: Any,
        extra_headers: dict[str, str] | None,
        force_json: bool,
        retry: bool,
    ) -> Any:
        url = f"{API_BASE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": API_USER_AGENT,
            "Accept-Language": ACCEPT_LANGUAGE,
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                allow_redirects=True,
            ) as response:
                if response.status == 401 and retry:
                    await self._refresh()
                    return await self._request_once(
                        method,
                        path,
                        params=params,
                        json=json,
                        extra_headers=extra_headers,
                        force_json=force_json,
                        retry=False,
                    )
                if response.status == 401:
                    raise BiedronkaAuthError("unauthorized")
                if response.status == 204:
                    return None
                if response.status >= 400:
                    body = await response.text()
                    _LOGGER.debug("API %s %s: %s", response.method, response.url, body[:500])
                    raise BiedronkaError(f"http_{response.status}")
                if force_json:
                    return _loads_json(await response.text())
                if response.content_type and "json" not in response.content_type:
                    return await response.text()
                return await response.json(content_type=None)
        except TimeoutError as err:
            raise BiedronkaCannotConnect("timeout") from err
        except aiohttp.ClientError as err:
            raise BiedronkaCannotConnect(str(err)) from err

    async def users_me(self, refresh: bool = False) -> dict[str, Any]:
        return await self.request("GET", "users/me/", params={"refresh": str(refresh).lower()})

    async def transactions(self, page: int = 1) -> dict[str, Any]:
        return await self.request("GET", "transactions/", params={"page": page})

    async def transaction_details(self, transaction_id: str) -> dict[str, Any]:
        return await self.request("GET", f"transactions/{transaction_id}/")

    async def e_receipt(self, transaction_id: str, output_format: str = "json") -> Any:
        """Download a fiscal e-receipt; the app uses output-format=json."""
        return await self.request(
            "GET",
            f"transactions/{transaction_id}/e-receipt/",
            headers={"output-format": output_format},
            force_json=True,
        )

    async def dashboard(self) -> Any:
        return await self.request("GET", "dashboards/dashboard/")

    async def carousel(self, carousel_id: str) -> dict[str, Any]:
        return await self.request("GET", f"promos/carousels/{carousel_id}/")

    async def shakeomat_assets(self) -> Any:
        return await self.request("GET", "promos/shakeomats/")

    async def coupons(self) -> dict[str, Any]:
        return await self.request("GET", "promos/coupons/")

    async def reveal_and_activate(self, offer_id: str) -> Any:
        return await self.request("PATCH", f"offers/{offer_id}/reveal-and-activate/")
