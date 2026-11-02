"""Sensors: card, transactions, vouchers, Shakeomat/Shakeomarka and card offers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BiedronkaConfigEntry
from .coordinator import (
    SECTION_LOYALTY,
    SECTION_MESSAGES,
    SECTION_SHAKEOMATS,
    SECTION_TRANSACTIONS,
    SECTION_USER,
    SECTION_VOUCHERS,
    WARSAW,
    BiedronkaCoordinator,
    _parse_dt,
    nearest_expiring_voucher,
)
from .entity import BiedronkaEntity
from .models import (
    KIND_SHAKEOMARKA,
    KIND_SHAKEOMAT,
    STATUS_AVAILABLE,
    STATUS_COOLDOWN,
    STATUS_NONE,
    VOUCHER_STATUS_READY,
)

PARALLEL_UPDATES = 0
MAX_LISTED_MESSAGES = 10


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
            BiedronkaVouchersSensor(coordinator),
            BiedronkaVoucherExpirySensor(coordinator),
            BiedronkaShakeomatSensor(coordinator, KIND_SHAKEOMAT),
            BiedronkaShakeomatSensor(coordinator, KIND_SHAKEOMARKA),
            BiedronkaShakeomatAvailableSensor(coordinator),
            BiedronkaLastShakeomatSensor(coordinator, KIND_SHAKEOMAT),
            BiedronkaLastShakeomatSensor(coordinator, KIND_SHAKEOMARKA),
            BiedronkaCardOffersSensor(coordinator),
            BiedronkaLoyaltyActionsSensor(coordinator),
            BiedronkaLoyaltyBalanceSensor(coordinator),
            BiedronkaStoreHoursSensor(coordinator),
            BiedronkaMessagesSensor(coordinator),
        ]
    )


class BiedronkaCardSensor(BiedronkaEntity, SensorEntity):
    _attr_translation_key = "card"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_card"

    @property
    def native_value(self) -> str | None:
        data = self.known_data(SECTION_USER)
        return data.user.get("card_number") if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_USER)
        user = data.user if data else {}
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
        data = self.known_data(SECTION_TRANSACTIONS)
        tx = data.last_transaction if data else None
        if not tx:
            return None
        return float(tx.get("total_price") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_TRANSACTIONS)
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
    def native_value(self) -> float | None:
        data = self.known_data(SECTION_TRANSACTIONS)
        return data.today_total if data else None


class BiedronkaReceiptsSensor(BiedronkaEntity, SensorEntity):
    """Up to five most recent receipts, with slim fiscal lines in attributes."""

    _attr_translation_key = "receipts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"receipts"})

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_receipts"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_TRANSACTIONS)
        return len(data.receipts) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_TRANSACTIONS)
        if not data:
            return {}
        return {"receipts": [receipt.as_dict() for receipt in data.receipts]}


class BiedronkaVouchersSensor(BiedronkaEntity, SensorEntity):
    """How many vouchers are ready to use, with every coupon listed in the attributes."""

    _attr_translation_key = "vouchers"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"vouchers"})

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_vouchers"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_VOUCHERS)
        if not data:
            return None
        return sum(1 for voucher in data.vouchers if voucher.status == VOUCHER_STATUS_READY)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_VOUCHERS)
        if not data:
            return {}
        return {"vouchers": [voucher.as_dict() for voucher in data.vouchers]}


class BiedronkaVoucherExpirySensor(BiedronkaEntity, SensorEntity):
    """Soonest valid_to among ready and pending vouchers that have not expired yet."""

    _attr_translation_key = "voucher_expiry"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_voucher_expiry"

    def _nearest(self):
        data = self.known_data(SECTION_VOUCHERS)
        if not data:
            return None
        return nearest_expiring_voucher(data.vouchers, datetime.now(WARSAW))

    @property
    def native_value(self) -> datetime | None:
        voucher = self._nearest()
        if not voucher:
            return None
        return _parse_dt(voucher.valid_to)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        voucher = self._nearest()
        if not voucher:
            return {}
        return {
            "number": voucher.number,
            "discount": voucher.discount,
            "conditions": voucher.conditions,
            "status": voucher.status,
        }


class BiedronkaShakeomatSensor(BiedronkaEntity, SensorEntity):
    """Closest Shakeomat (or Shakeomarka) card, with every pending one listed."""

    def __init__(self, coordinator: BiedronkaCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_unique_id = f"{self._card}_{kind}"

    @property
    def native_value(self) -> str | None:
        data = self.known_data(SECTION_SHAKEOMATS)
        if not data:
            return None
        current = data.current_of(self._kind)
        return current.status if current else STATUS_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_SHAKEOMATS)
        if not data:
            return {}
        cards = data.shakeomats_of(self._kind)
        upcoming = [
            offer.available_from for offer in cards if offer.status == STATUS_COOLDOWN
        ]
        attributes: dict[str, Any] = {
            "pending_count": len(cards),
            "available_count": sum(
                1 for offer in cards if offer.status == STATUS_AVAILABLE
            ),
            "next_available_from": upcoming[0] if upcoming else None,
            "offers": [offer.as_dict() for offer in cards],
        }
        current = data.current_of(self._kind)
        if current:
            attributes.update(current.as_dict())
        return attributes


class BiedronkaShakeomatAvailableSensor(BiedronkaEntity, SensorEntity):
    """How many Shakeomat offers can be revealed right now."""

    _attr_translation_key = "shakeomat_available"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_shakeomat_available"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_SHAKEOMATS)
        return len(data.available_shakeomats) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_SHAKEOMATS)
        if not data:
            return {}
        return {
            "offer_ids": [
                offer.offer_id for offer in data.available_shakeomats if offer.offer_id
            ]
        }


class BiedronkaLastShakeomatSensor(BiedronkaEntity, SensorEntity):
    """Most recently revealed offer; earlier ones stay in the history attribute."""

    def __init__(self, coordinator: BiedronkaCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_translation_key = f"{kind}_last_offer"
        self._attr_unique_id = f"{self._card}_{kind}_last_offer"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        rewards = data.rewards_of(self._kind) if data else []
        if not rewards:
            return None
        return (rewards[0].name or rewards[0].offer_id)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        rewards = data.rewards_of(self._kind)
        attributes: dict[str, Any] = {
            "history": [reward.as_dict() for reward in rewards]
        }
        if rewards:
            attributes.update(rewards[0].as_dict())
        return attributes


class BiedronkaCardOffersSensor(BiedronkaEntity, SensorEntity):
    """Active "Tylko dla Ciebie" offers Biedronka assigned to this card."""

    _attr_translation_key = "card_offers"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_card_offers"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_SHAKEOMATS)
        return len(data.card_offers) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_SHAKEOMATS)
        if not data:
            return {}
        return {"offers": [offer.as_dict() for offer in data.card_offers]}


class BiedronkaLoyaltyActionsSensor(BiedronkaEntity, SensorEntity):
    """Active sticker / points campaigns ("Akcja"), all of them in `actions`."""

    _attr_translation_key = "loyalty_actions"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_loyalty_actions"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_LOYALTY)
        return len(data.active_loyalty_actions) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_LOYALTY)
        if not data:
            return {}
        return {"actions": [action.as_dict() for action in data.loyalty_actions]}


class BiedronkaLoyaltyBalanceSensor(BiedronkaEntity, SensorEntity):
    """Stickers or points collected in the current campaign."""

    _attr_translation_key = "loyalty_balance"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_loyalty_balance"

    def _action(self):
        data = self.known_data(SECTION_LOYALTY)
        actions = data.active_loyalty_actions if data else []
        return next((a for a in actions if a.balance is not None), None)

    @property
    def native_value(self) -> float | None:
        action = self._action()
        return action.balance if action else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        action = self._action()
        data = self.known_data(SECTION_LOYALTY)
        if not action or not data:
            return {}
        details = action.as_dict()
        return {
            "action": action.name,
            **{
                key: details[key]
                for key in (
                    "unit",
                    "collect_until",
                    "end_on",
                    "available_rewards",
                    "next_reward",
                    "missing_to_next_reward",
                    "rewards",
                )
            },
            "history": [
                move.as_dict()
                for move in data.wallet_moves
                if move.wallet_id == action.wallet_id
            ],
        }


class BiedronkaStoreHoursSensor(BiedronkaEntity, SensorEntity):
    """Today's opening hours of the store chosen in the app."""

    _attr_translation_key = "store_hours"

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_store_hours"

    @property
    def native_value(self) -> str | None:
        data = self.known_data(SECTION_USER)
        store = data.store if data else None
        return store.hours(dt_util.now().date().isoformat()) if store else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_USER)
        store = data.store if data else None
        return store.as_dict(dt_util.now().date().isoformat()) if store else {}


class BiedronkaMessagesSensor(BiedronkaEntity, SensorEntity):
    """Messages in the app's inbox; the newest one in the attributes."""

    _attr_translation_key = "messages"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BiedronkaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._card}_messages"

    @property
    def native_value(self) -> int | None:
        data = self.known_data(SECTION_MESSAGES)
        return len(data.messages) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.known_data(SECTION_MESSAGES)
        if not data or not data.messages:
            return {}
        return {
            "latest": data.messages[0].as_dict(),
            "messages": [
                {key: message.as_dict()[key] for key in ("title", "preview", "created_at")}
                for message in data.messages[:MAX_LISTED_MESSAGES]
            ],
        }
