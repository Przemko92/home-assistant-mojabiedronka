"""Whether the store chosen in the Biedronka app is open right now."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BiedronkaConfigEntry
from .coordinator import SECTION_USER, BiedronkaCoordinator
from .entity import BiedronkaEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BiedronkaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([BiedronkaStoreOpenSensor(entry.runtime_data)])


class BiedronkaStoreOpenSensor(BiedronkaEntity, BinarySensorEntity):
    """On while the app's chosen store is open (refreshed with each poll)."""

    _attr_translation_key = "store_open"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_store_open"

    @property
    def is_on(self) -> bool | None:
        data = self.known_data(SECTION_USER)
        store = data.store if data else None
        return store.open_at(dt_util.now()) if store else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_USER)
        store = data.store if data else None
        if not store:
            return {}
        today = dt_util.now().date().isoformat()
        return {
            "code": store.code,
            "address": store.address,
            "today": store.hours(today),
            "target_hour": store.target_hour,
        }
