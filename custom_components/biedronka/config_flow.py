"""Config flow for Biedronka — Browser Companion only."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from ha_browser_companion import CompanionLoginFlow, CompanionStart, captured_query
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import BiedronkaApi
from .auth import KeycloakLogin, auth_code_from_redirect
from .const import (
    COMPANION_WAIT,
    CONF_ACCESS_TOKEN,
    CONF_AUTO_SHAKEOMAT,
    CONF_CARD_NUMBER,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_AUTO_SHAKEOMAT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .exceptions import BiedronkaAuthError, BiedronkaCannotConnect


class BiedronkaConfigFlow(CompanionLoginFlow, ConfigFlow, domain=DOMAIN):
    """Sign in through the Browser Companion add-on."""

    VERSION = 1
    companion_client_id = DOMAIN

    def __init__(self) -> None:
        self._login: KeycloakLogin | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.companion_supervisor_present():
            return self.async_abort(reason="companion_requires_supervisor")
        return await self.async_step_companion()

    async def async_companion_start(self) -> CompanionStart:
        if self._login is None:
            self._login = KeycloakLogin.create()
        return CompanionStart(start_url=self._login.prepare(), wait=COMPANION_WAIT)

    async def async_companion_finish(
        self, captured: dict[str, Any]
    ) -> ConfigFlowResult:
        code = captured_query(captured, "code")
        if not code:
            try:
                code = auth_code_from_redirect(str(captured.get("url") or ""))
            except BiedronkaAuthError:
                return await self.async_step_companion_failed()
        if self._login is None or not self._login.verifier:
            return await self.async_step_companion_failed()
        try:
            tokens = await self._login.exchange_code(str(code), self._login.verifier)
            return await self._async_finish(tokens)
        except BiedronkaCannotConnect:
            return self.async_abort(reason="cannot_connect")
        except BiedronkaAuthError:
            return await self.async_step_companion_failed()

    async def async_companion_on_close(self) -> None:
        if self._login is not None:
            await self._login.close()
            self._login = None

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_user()

    async def _async_finish(self, tokens: dict[str, str]) -> ConfigFlowResult:
        session = async_get_clientsession(self.hass)
        api = BiedronkaApi(
            session, tokens[CONF_ACCESS_TOKEN], tokens[CONF_REFRESH_TOKEN]
        )
        try:
            user = await api.users_me()
        except BiedronkaAuthError:
            return self.async_abort(reason="invalid_token")
        except BiedronkaCannotConnect:
            return self.async_abort(reason="cannot_connect")

        phone = str((user or {}).get("phone") or "") or None
        card = str((user or {}).get("card_number") or "unknown")
        await self.async_set_unique_id(card)
        if self._reauth_entry:
            if self._reauth_entry.unique_id not in (None, card):
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={
                    CONF_ACCESS_TOKEN: api.access_token,
                    CONF_REFRESH_TOKEN: api.refresh_token,
                    CONF_PHONE: phone,
                    CONF_CARD_NUMBER: card,
                },
            )
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"Biedronka {card}",
            data={
                CONF_ACCESS_TOKEN: api.access_token,
                CONF_REFRESH_TOKEN: api.refresh_token,
                CONF_PHONE: phone,
                CONF_CARD_NUMBER: card,
            },
            options={
                CONF_AUTO_SHAKEOMAT: DEFAULT_AUTO_SHAKEOMAT,
                CONF_SCAN_INTERVAL: int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> OptionsFlow:
        return BiedronkaOptionsFlow()


class BiedronkaOptionsFlow(OptionsFlow):
    """Poll interval and auto Shakeomat."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=int(
                        options.get(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL.total_seconds() // 60,
                        )
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=120,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(
                    CONF_AUTO_SHAKEOMAT,
                    default=options.get(CONF_AUTO_SHAKEOMAT, DEFAULT_AUTO_SHAKEOMAT),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
