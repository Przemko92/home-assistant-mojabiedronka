"""Sensors: card, last transaction, today spend, Shakeomat status."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BiedronkaConfigEntry
from .coordinator import BiedronkaCoordinator
from .entity import BiedronkaEntity
from .models import STATUS_COOLDOWN, STATUS_NONE

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BiedronkaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            BiedronkaCardSensor(coordinator),
            BiedronkaLastTransactionSensor(coordinator),
            BiedronkaTodaySpendSensor(coordinator),
            BiedronkaReceiptsSensor(coordinator),
            BiedronkaShakeomatSensor(coordinator),
            BiedronkaShakeomatAvailableSensor(coordinator),
            BiedronkaLastShakeomatSensor(coordinator),
        ]
    )


class BiedronkaCardSensor(BiedronkaEntity, SensorEntity):
    _attr_translation_key = "card"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_card"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.user.get("card_number") if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        user = self.coordinator.data.user if self.coordinator.data else {}
        store = user.get("store") if isinstance(user.get("store"), dict) else {}
        return {
            "first_name": user.get("first_name"),
            "phone_number": user.get("phone_number"),
            "store_id": store.get("id") if store else user.get("store"),
            "store_name": store.get("name") or store.get("display_name"),
        }


class BiedronkaLastTransactionSensor(BiedronkaEntity, SensorEntity):
    _attr_translation_key = "last_transaction"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "PLN"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_last_transaction"

    @property
    def native_value(self) -> float | None:
        tx = self.coordinator.data.last_transaction if self.coordinator.data else None
        if not tx:
            return None
        return float(tx.get("total_price") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data or not data.last_transaction:
            return {}
        tx = data.last_transaction
        details = data.last_details or {}
        items = details.get("items") or []
        slim_items = [
            {
                "name": item.get("name"),
                "ean": item.get("ean"),
                "quantity": item.get("quantity"),
                "unit_price": item.get("unit_price"),
                "total_price": item.get("total_price"),
                "total_discount": item.get("total_discount"),
            }
            for item in items
            if isinstance(item, dict)
        ]
        return {
            "id": tx.get("id"),
            "date": tx.get("date"),
            "store_name": tx.get("store_name") or details.get("store_name"),
            "receipt_num": tx.get("receipt_num") or details.get("receipt_num"),
            "total_discount": details.get("total_discount"),
            "e_receipt": tx.get("is_e_receipt_available"),
            "items": slim_items,
        }


class BiedronkaTodaySpendSensor(BiedronkaEntity, SensorEntity):
    _attr_translation_key = "today_spend"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "PLN"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_today_spend"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.today_total if self.coordinator.data else 0.0


class BiedronkaReceiptsSensor(BiedronkaEntity, SensorEntity):
    """Up to five most recent receipts, with slim fiscal lines in attributes."""

    _attr_translation_key = "receipts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"receipts"})

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_receipts"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.receipts) if self.coordinator.data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return {"receipts": [receipt.as_dict() for receipt in data.receipts]}


class BiedronkaShakeomatSensor(BiedronkaEntity, SensorEntity):
    """Closest Shakeomat offer, with every pending one listed in the attributes."""

    _attr_translation_key = "shakeomat"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_shakeomat"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        current = data.current_shakeomat if data else None
        return current.status if current else STATUS_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        offers = [offer.as_dict() for offer in data.shakeomats]
        upcoming = [
            offer.available_from
            for offer in data.shakeomats
            if offer.status == STATUS_COOLDOWN
        ]
        attributes: dict[str, Any] = {
            "pending_count": len(data.shakeomats),
            "available_count": len(data.available_shakeomats),
            "next_available_from": upcoming[0] if upcoming else None,
            "offers": offers,
        }
        if data.current_shakeomat:
            attributes.update(data.current_shakeomat.as_dict())
        return attributes


class BiedronkaShakeomatAvailableSensor(BiedronkaEntity, SensorEntity):
    """How many Shakeomat offers can be revealed right now."""

    _attr_translation_key = "shakeomat_available"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_shakeomat_available"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.available_shakeomats) if self.coordinator.data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "offer_ids": [
                offer.offer_id for offer in data.available_shakeomats if offer.offer_id
            ]
        }


class BiedronkaLastShakeomatSensor(BiedronkaEntity, SensorEntity):
    """Most recently revealed offer; earlier ones stay in the history attribute."""

    _attr_translation_key = "shakeomat_last_offer"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_shakeomat_last_offer"

    @property
    def native_value(self) -> str | None:
        reward = self.coordinator.data.last_reward if self.coordinator.data else None
        if not reward:
            return None
        return (reward.name or reward.offer_id)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        attributes: dict[str, Any] = {
            "history": [reward.as_dict() for reward in data.reward_history]
        }
        if data.last_reward:
            attributes.update(data.last_reward.as_dict())
        return attributes
