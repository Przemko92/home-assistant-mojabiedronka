"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CARD_NUMBER, DOMAIN
from .coordinator import BiedronkaCoordinator
from .models import BiedronkaData


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
            name=device_name(self._card),
            model="Karta lojalnościowa (nieoficjalne API)",
        )

    def known_data(self, section: str) -> BiedronkaData | None:
        """Coordinator data when `section` still has a correct result."""
        data = self.coordinator.data
        if data is None or section not in data.known:
            return None
        return data


def device_name(card: str) -> str:
    """'Moja Biedronka 5678' — the card suffix keeps several accounts apart."""
    if card.isdigit() and len(card) >= 4:
        return f"Moja Biedronka {card[-4:]}"
    return "Moja Biedronka"
