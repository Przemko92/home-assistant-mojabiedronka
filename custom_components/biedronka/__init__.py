"""Unofficial Biedronka Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BiedronkaApi
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CARD_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import BiedronkaCoordinator

type BiedronkaConfigEntry = ConfigEntry[BiedronkaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BiedronkaConfigEntry) -> bool:
    _async_remove_slot_entities(hass, entry)
    session = async_get_clientsession(hass)

    async def _save_tokens(tokens: dict[str, str]) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tokens[CONF_ACCESS_TOKEN],
                CONF_REFRESH_TOKEN: tokens[CONF_REFRESH_TOKEN],
            },
        )

    api = BiedronkaApi(
        session,
        entry.data[CONF_ACCESS_TOKEN],
        entry.data[CONF_REFRESH_TOKEN],
        save_tokens=_save_tokens,
    )
    coordinator = BiedronkaCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _async_remove_slot_entities(hass: HomeAssistant, entry: BiedronkaConfigEntry) -> None:
    """Drop the pre-rework Shakeomat 1/2 entities, which no longer have a source."""
    card = entry.data.get(CONF_CARD_NUMBER) or entry.unique_id or entry.entry_id
    registry = er.async_get(hass)
    legacy = {
        "sensor": (f"{card}_shakeomat_1", f"{card}_shakeomat_2"),
        "button": (f"{card}_shakeomat_1_activate", f"{card}_shakeomat_2_activate"),
    }
    for domain, unique_ids in legacy.items():
        for unique_id in unique_ids:
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id:
                registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: BiedronkaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: BiedronkaCoordinator = entry.runtime_data
    minutes = entry.options.get(CONF_SCAN_INTERVAL)
    coordinator.update_interval = (
        timedelta(minutes=int(minutes)) if minutes else DEFAULT_SCAN_INTERVAL
    )
