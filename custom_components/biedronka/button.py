"""Buttons to activate Shakeomat offers."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BiedronkaConfigEntry
from .coordinator import BiedronkaCoordinator
from .entity import BiedronkaEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BiedronkaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([BiedronkaShakeomatButton(entry.runtime_data)])


class BiedronkaShakeomatButton(BiedronkaEntity, ButtonEntity):
    """Reveals every Shakeomat offer that is ready, however many there are."""

    _attr_translation_key = "shakeomat_activate"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_shakeomat_activate"

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        return any(
            offer.offer_id for offer in self.coordinator.data.available_shakeomats
        )

    async def async_press(self) -> None:
        await self.coordinator.async_activate_all()
