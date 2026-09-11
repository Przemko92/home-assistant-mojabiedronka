"""Dataclasses for coordinator payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_AVAILABLE = "available"
STATUS_COOLDOWN = "cooldown"
STATUS_EXPIRED = "expired"
STATUS_CLAIMED = "claimed"
STATUS_NONE = "none"

RECEIPT_SOURCE_E_RECEIPT = "e_receipt"
RECEIPT_SOURCE_DETAILS = "details"


@dataclass
class ShakeomatOffer:
    """A single Shakeomat card as served by the dashboard carousels."""

    offer_id: str | None = None
    status: str = STATUS_NONE
    item_type: str | None = None
    offer_type: str | None = None
    available_from: str | None = None
    available_to: str | None = None
    assets_id: str | None = None
    assets_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "status": self.status,
            "item_type": self.item_type,
            "offer_type": self.offer_type,
            "available_from": self.available_from,
            "available_to": self.available_to,
            "assets_id": self.assets_id,
            "name": self.assets_name,
        }


@dataclass
class ShakeomatReward:
    """An offer revealed by shaking, kept even after it leaves the dashboard."""

    offer_id: str
    claimed_at: str | None = None
    name: str | None = None
    details: str | None = None
    description: str | None = None
    price: str | None = None
    discount: str | None = None
    image_url: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "claimed_at": self.claimed_at,
            "name": self.name,
            "details": self.details,
            "description": self.description,
            "price": self.price,
            "discount": self.discount,
            "image_url": self.image_url,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShakeomatReward | None:
        offer_id = data.get("offer_id")
        if not offer_id:
            return None
        return cls(
            offer_id=str(offer_id),
            claimed_at=data.get("claimed_at"),
            name=data.get("name"),
            details=data.get("details"),
            description=data.get("description"),
            price=data.get("price"),
            discount=data.get("discount"),
            image_url=data.get("image_url"),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
        )


@dataclass
class Receipt:
    """A receipt JSON payload fetched on coordinator refresh."""

    id: str
    date: str | None = None
    store_name: str | None = None
    receipt_num: str | None = None
    total_price: float | None = None
    source: str = RECEIPT_SOURCE_DETAILS
    payload: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "store_name": self.store_name,
            "receipt_num": self.receipt_num,
            "total_price": self.total_price,
            "source": self.source,
            "payload": self.payload,
        }


@dataclass
class BiedronkaData:
    user: dict[str, Any] = field(default_factory=dict)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    last_transaction: dict[str, Any] | None = None
    last_details: dict[str, Any] | None = None
    today_total: float = 0.0
    receipts: list[Receipt] = field(default_factory=list)
    shakeomats: list[ShakeomatOffer] = field(default_factory=list)
    current_shakeomat: ShakeomatOffer | None = None
    available_shakeomats: list[ShakeomatOffer] = field(default_factory=list)
    last_reward: ShakeomatReward | None = None
    reward_history: list[ShakeomatReward] = field(default_factory=list)
