"""Keycloak PKCE + SMS login and token refresh."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import aiohttp

from .const import (
    ACCEPT_LANGUAGE,
    AUTH_BASE,
    CLIENT_ID,
    KEYCLOAK_USER_AGENT,
    REALM,
    REDIRECT_URI,
)
from .exceptions import (
    BiedronkaAuthError,
    BiedronkaCannotConnect,
    BiedronkaCaptchaError,
    BiedronkaInvalidSmsError,
    BiedronkaSmsBlockedError,
)
from .html_form import first_form, parse_html

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = f"{AUTH_BASE}/realms/{REALM}/protocol/openid-connect/token"
AUTH_URL = f"{AUTH_BASE}/realms/{REALM}/protocol/openid-connect/auth"

_MAX_REDIRECTS = 12


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return {}


def normalize_pl_phone(raw: str) -> tuple[str, str]:
    """Return (national 9-digit, username without plus e.g. 48…)."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("48") and len(digits) == 11:
        national = digits[2:]
        return national, digits
    if len(digits) == 9:
        return digits, f"48{digits}"
    raise BiedronkaAuthError("invalid_phone")


def authorization_url(challenge: str) -> str:
    """Keycloak authorize URL for the CMA20 public client."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def auth_code_from_redirect(value: str) -> str:
    """Extract the OAuth code from an app:// redirect or a raw code."""
    raw = value.strip().strip('"').strip("'")
    if not raw:
        raise BiedronkaAuthError("missing_auth_code")
    app_idx = raw.find("app://")
    if app_idx >= 0:
        raw = raw[app_idx:].split()[0].rstrip(".,;\"'")
    if "://" in raw or raw.startswith("app:") or "code=" in raw:
        return _code_from_redirect(raw)
    return raw


class KeycloakLogin:
    """Browser-less Keycloak session for the CMA20 client."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self.verifier: str | None = None
        self.auth_url: str | None = None
        self._form_action: str | None = None
        self._form_inputs: dict[str, str] = {}
        self._owns_session = False

    @classmethod
    def create(cls) -> KeycloakLogin:
        jar = aiohttp.CookieJar(unsafe=True)
        session = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={
                "User-Agent": KEYCLOAK_USER_AGENT,
                "Accept-Language": f"{ACCEPT_LANGUAGE},pl;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        login = cls(session)
        login._owns_session = True
        return login

    async def close(self) -> None:
        if self._owns_session and not self._session.closed:
            await self._session.close()

    def prepare(self) -> str:
        """Generate PKCE and return the authorize URL (no HTTP yet)."""
        if not self.verifier or not self.auth_url:
            self.verifier, challenge = generate_pkce()
            self.auth_url = authorization_url(challenge)
        return self.auth_url

    async def start(self) -> None:
        """Open the authorization page (phone step)."""
        url = self.prepare()
        html, final_url = await self._request("GET", url)
        parsed = parse_html(html, final_url)
        form = first_form(parsed)
        if form is None or not parsed["has_phone"]:
            raise BiedronkaAuthError("login_form_missing")
        self._form_action = form["action"]
        self._form_inputs = dict(form["inputs"])

    async def submit_phone(self, phone: str) -> None:
        """POST the phone number. Next page should ask for SMS."""
        national, username = normalize_pl_phone(phone)
        if not self._form_action:
            await self.start()
        assert self._form_action is not None
        payload = dict(self._form_inputs)
        payload["phoneNumber"] = national
        payload["username"] = username
        payload.setdefault("login", "DALEJ")
        html, final_url = await self._request("POST", self._form_action, data=payload)
        parsed = parse_html(html, final_url)
        if parsed["sms_blocked"]:
            raise BiedronkaSmsBlockedError("sms_blocked")
        if parsed["has_phone"] and parsed["has_turnstile"]:
            raise BiedronkaCaptchaError("captcha")
        if parsed["has_phone"]:
            message = "; ".join(parsed["errors"]) or "phone_rejected"
            raise BiedronkaAuthError(message)
        form = first_form(parsed)
        if form is None or not parsed["has_sms"]:
            if parsed["has_turnstile"]:
                raise BiedronkaCaptchaError("captcha")
            raise BiedronkaAuthError("sms_form_missing")
        self._form_action = form["action"]
        self._form_inputs = dict(form["inputs"])

    async def submit_sms(self, sms_code: str) -> dict[str, str]:
        """POST the SMS code and exchange the authorization code for tokens."""
        if not self._form_action or not self.verifier:
            raise BiedronkaAuthError("login_not_started")
        payload = dict(self._form_inputs)
        code = "".join(ch for ch in sms_code if ch.isdigit())
        assigned = False
        for name in list(payload):
            lowered = name.lower()
            if any(key in lowered for key in ("sms", "otp", "code", "otc")) and name.lower() not in {
                "session_code",
                "code_id",
            }:
                payload[name] = code
                assigned = True
        if not assigned:
            payload["smsCode"] = code
            payload["code"] = code
        payload.setdefault("login", "Zaloguj się")
        result = await self._request_maybe_redirect("POST", self._form_action, data=payload)
        if isinstance(result, str) and result.startswith("app://"):
            return await self.exchange_code(_code_from_redirect(result), self.verifier)
        html, final_url = result  # type: ignore[misc]
        parsed = parse_html(html, final_url)
        if parsed["sms_blocked"]:
            raise BiedronkaSmsBlockedError("sms_blocked")
        errors = "; ".join(parsed["errors"])
        if parsed["has_sms"] or parsed["has_phone"]:
            raise BiedronkaInvalidSmsError(errors or "invalid_sms")
        raise BiedronkaAuthError(errors or "login_failed")

    async def exchange_code(self, code: str, verifier: str) -> dict[str, str]:
        data = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }
        return await self._token_request(data)

    async def _request(
        self, method: str, url: str, data: dict[str, str] | None = None
    ) -> tuple[str, str]:
        result = await self._request_maybe_redirect(method, url, data)
        if isinstance(result, str):
            raise BiedronkaAuthError("unexpected_app_redirect")
        return result

    async def _request_maybe_redirect(
        self, method: str, url: str, data: dict[str, str] | None = None
    ) -> tuple[str, str] | str:
        current_method = method
        current_url = url
        current_data = data
        try:
            for _ in range(_MAX_REDIRECTS):
                if current_url.startswith("app://"):
                    return current_url
                kwargs: dict[str, Any] = {"allow_redirects": False}
                if current_method == "POST":
                    kwargs["data"] = current_data
                    kwargs["headers"] = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": AUTH_BASE,
                        "Referer": current_url,
                    }
                async with self._session.request(current_method, current_url, **kwargs) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise BiedronkaAuthError("missing_redirect")
                        current_url = urljoin(str(resp.url), location)
                        current_method = "GET"
                        current_data = None
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        _LOGGER.debug("Keycloak HTTP %s: %s", resp.status, body[:500])
                        raise BiedronkaCannotConnect(f"keycloak_http_{resp.status}")
                    return await resp.text(), str(resp.url)
        except TimeoutError as err:
            raise BiedronkaCannotConnect("timeout") from err
        except aiohttp.ClientError as err:
            raise BiedronkaCannotConnect(str(err)) from err
        raise BiedronkaAuthError("too_many_redirects")

    async def _token_request(self, data: dict[str, str]) -> dict[str, str]:
        try:
            async with self._session.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                payload = await resp.json(content_type=None)
                if resp.status >= 400:
                    _LOGGER.debug("Token error %s: %s", resp.status, payload)
                    raise BiedronkaAuthError("token_rejected")
        except TimeoutError as err:
            raise BiedronkaCannotConnect("timeout") from err
        except aiohttp.ClientError as err:
            raise BiedronkaCannotConnect(str(err)) from err
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        if not access or not refresh:
            raise BiedronkaAuthError("token_rejected")
        return {"access_token": access, "refresh_token": refresh}


async def refresh_tokens(
    session: aiohttp.ClientSession, refresh_token: str
) -> dict[str, str]:
    """Refresh using the public CMA20 client (no secret)."""
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": refresh_token,
    }
    try:
        async with session.post(
            TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": KEYCLOAK_USER_AGENT,
            },
        ) as resp:
            payload = await resp.json(content_type=None)
            if resp.status >= 400:
                raise BiedronkaAuthError("token_rejected")
    except TimeoutError as err:
        raise BiedronkaCannotConnect("timeout") from err
    except aiohttp.ClientError as err:
        raise BiedronkaCannotConnect(str(err)) from err
    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or refresh_token
    if not access:
        raise BiedronkaAuthError("token_rejected")
    return {"access_token": access, "refresh_token": refresh}


def _code_from_redirect(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if not query.get("code") and parsed.fragment:
        query = parse_qs(parsed.fragment)
    code = (query.get("code") or [None])[0]
    if not code:
        raise BiedronkaAuthError("missing_auth_code")
    return code
