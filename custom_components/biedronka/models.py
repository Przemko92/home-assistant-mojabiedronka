"""Dataclasses for coordinator payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

STATUS_AVAILABLE = "available"
STATUS_COOLDOWN = "cooldown"
STATUS_EXPIRED = "expired"
STATUS_CLAIMED = "claimed"
STATUS_NONE = "none"

RECEIPT_SOURCE_E_RECEIPT = "e_receipt"
RECEIPT_SOURCE_DETAILS = "details"

_SELL_LINE_KEYS = ("name", "vatId", "price", "total", "quantity", "isStorno")
_DISCOUNT_LINE_KEYS = (
    "base",
    "value",
    "isDiscount",
    "isPercent",
    "isStorno",
    "vatId",
)
_DISCOUNT_SUMMARY_KEYS = ("discounts",)
_VAT_RATE_KEYS = ("vatId", "vatRate", "vatSale", "vatAmount")
_SUM_IN_CURRENCY_KEYS = ("fiscalTotal", "totalWithPacks", "currency")
_LINE_TYPES = (
    "sellLine",
    "discountLine",
    "discountSummary",
    "vatSummary",
    "sumInCurrency",
)


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data}


def _as_json_object(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return payload


def _extract_receipt_lines(payload: Any) -> list[Any]:
    payload = _as_json_object(payload)
    if isinstance(payload, list):
        if any(
            isinstance(item, dict) and any(key in item for key in _LINE_TYPES)
            for item in payload
        ):
            return payload
        for item in payload:
            found = _extract_receipt_lines(item)
            if found:
                return found
        return []
    if not isinstance(payload, dict):
        return []
    for key in ("lines", "receiptLines"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for key in ("receipt", "data", "payload", "json"):
        nested = payload.get(key)
        if nested is not None:
            found = _extract_receipt_lines(nested)
            if found:
                return found
    items = payload.get("items")
    if isinstance(items, list):
        return _lines_from_details_items(items)
    return []


def _lines_from_details_items(items: list[Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sell = _pick(item, _SELL_LINE_KEYS)
        if "price" not in sell and item.get("unit_price") is not None:
            sell["price"] = item["unit_price"]
        if "total" not in sell and item.get("total_price") is not None:
            sell["total"] = item["total_price"]
        if sell:
            lines.append({"sellLine": sell})
    return lines


def _slim_vat_summary(data: dict[str, Any]) -> dict[str, Any]:
    slim = _pick(data, ("currency",))
    rates = data.get("vatRatesSummary")
    if isinstance(rates, list):
        slim_rates = [
            _pick(rate, _VAT_RATE_KEYS) for rate in rates if isinstance(rate, dict)
        ]
        slim["vatRatesSummary"] = [rate for rate in slim_rates if rate]
    return slim


def _slim_line(line: Any) -> dict[str, Any] | None:
    if not isinstance(line, dict):
        return None
    slim: dict[str, Any] = {}
    sell = line.get("sellLine")
    if isinstance(sell, dict):
        picked = _pick(sell, _SELL_LINE_KEYS)
        if picked:
            slim["sellLine"] = picked
    discount = line.get("discountLine")
    if isinstance(discount, dict):
        picked = _pick(discount, _DISCOUNT_LINE_KEYS)
        if picked:
            slim["discountLine"] = picked
    summary = line.get("discountSummary")
    if isinstance(summary, dict):
        picked = _pick(summary, _DISCOUNT_SUMMARY_KEYS)
        if picked:
            slim["discountSummary"] = picked
    vat = line.get("vatSummary")
    if isinstance(vat, dict):
        picked = _slim_vat_summary(vat)
        if picked:
            slim["vatSummary"] = picked
    total = line.get("sumInCurrency")
    if isinstance(total, dict):
        picked = _pick(total, _SUM_IN_CURRENCY_KEYS)
        if picked:
            slim["sumInCurrency"] = picked
    return slim or None


def slim_receipt_lines(payload: Any) -> list[dict[str, Any]]:
    """Keep only the fiscal line types and fields exposed on the receipts sensor."""
    slimmed: list[dict[str, Any]] = []
    for line in _extract_receipt_lines(payload):
        slim = _slim_line(line)
        if slim:
            slimmed.append(slim)
    return slimmed


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
    """A receipt fetched on coordinator refresh, with a slim fiscal line list."""

    id: str
    date: str | None = None
    store_name: str | None = None
    receipt_num: str | None = None
    total_price: float | None = None
    source: str = RECEIPT_SOURCE_DETAILS
    lines: list[dict[str, Any]] = field(default_factory=list)
    payload: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "store_name": self.store_name,
            "receipt_num": self.receipt_num,
            "total_price": self.total_price,
            "source": self.source,
            "lines": self.lines,
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
