"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CARD_NUMBER, DOMAIN
from .coordinator import BiedronkaCoordinator


class BiedronkaEntity(CoordinatorEntity[BiedronkaCoordinator]):
    """Device-bound entity."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        card = coordinator.entry.data.get(CONF_CARD_NUMBER) or coordinator.entry.unique_id or coordinator.entry.entry_id
        self._card = str(card)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._card)},
            manufacturer="Biedronka",
            name="Moja Biedronka",
            model="Karta lojalnościowa (nieoficjalne API)",
        )
