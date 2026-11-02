"""Dataclasses for coordinator payloads."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

STATUS_AVAILABLE = "available"
STATUS_COOLDOWN = "cooldown"
STATUS_EXPIRED = "expired"
STATUS_CLAIMED = "claimed"
STATUS_NONE = "none"

REWARD_SOURCE_HA = "home_assistant"
REWARD_SOURCE_APP = "app"
# Offers Biedronka assigns on its own ("Tylko dla Ciebie"), never shaken.
REWARD_SOURCE_PERSONAL = "personal"

KIND_SHAKEOMAT = "shakeomat"
KIND_SHAKEOMARKA = "shakeomarka"
KIND_CARD = "card"
KIND_VOUCHER = "voucher"
KIND_LOYALTY = "loyalty"
KIND_MESSAGE = "message"
KIND_STICKERS = "stickers"
KIND_REWARD = "loyalty_reward"
# Every kind that gets a "new ..." event entity.
OFFER_KINDS = (
    KIND_SHAKEOMAT,
    KIND_SHAKEOMARKA,
    KIND_CARD,
    KIND_VOUCHER,
    KIND_LOYALTY,
    KIND_MESSAGE,
    KIND_STICKERS,
    KIND_REWARD,
)

# The live API says "ongoing"; the app's own enum knows "ACTIVE".
LOYALTY_ACTIVE_STATUSES = frozenset({"ongoing", "active"})
_LOYALTY_UNITS = {"CARRIER": "naklejki", "DOTS": "punkty"}
_MAX_NUMBERED_AWARDS = 9
# One / 2-4 / many, for "1 naklejka", "3 naklejki", "10 naklejek".
_UNIT_FORMS = {
    "naklejki": ("naklejka", "naklejki", "naklejek"),
    "punkty": ("punkt", "punkty", "punktów"),
}

VOUCHER_STATUS_READY = "READY_TO_USE"
VOUCHER_STATUS_PENDING = "PENDING"
VOUCHER_STATUS_EXPIRED = "EXPIRED"
VOUCHER_STATUS_BURNED = "BURNED"
EXPIRING_VOUCHER_STATUSES = frozenset({VOUCHER_STATUS_READY, VOUCHER_STATUS_PENDING})

_LOCALIZED_KEYS = ("pl", "en", "sk")
_WARSAW = ZoneInfo("Europe/Warsaw")

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


def localized_text(value: Any) -> str | None:
    """Pick Polish, then English, then Slovak from an app translation object."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in _LOCALIZED_KEYS:
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


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

    @property
    def kind(self) -> str:
        if (self.item_type or "").upper() == "SHAKEOMARKA":
            return KIND_SHAKEOMARKA
        return KIND_SHAKEOMAT

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
    """An offer revealed by shaking, kept even after it leaves the dashboard.

    `source` is "home_assistant" when this integration revealed it, "app"
    when it was shaken in the phone app and later found on the dashboard, and
    "personal" for a "Tylko dla Ciebie" offer that needs no shaking at all.
    """

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
    regular_price: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    regular_unit_price: str | None = None
    omnibus_price: str | None = None
    discount_to_omnibus_price: str | None = None
    price_tag_info: str | None = None
    price_tag_note: str | None = None
    limit_message_short: str | None = None
    regular_price_line: str | None = None
    omnibus_price_line: str | None = None
    deposit: str | None = None
    origin_country: str | None = None
    ean: str | None = None
    offer_type: str | None = None
    promotion_type: str | None = None
    badges: list[str] = field(default_factory=list)
    promo_id: str | None = None
    source: str | None = None

    @property
    def kind(self) -> str:
        if (self.offer_type or "").upper() == "SHAKEOMARKA":
            return KIND_SHAKEOMARKA
        if self.source == REWARD_SOURCE_PERSONAL:
            return KIND_CARD
        return KIND_SHAKEOMAT

    @property
    def key(self) -> str:
        """Stable across HA and the app: the dashboard id once it is known."""
        return self.promo_id or self.offer_id

    @property
    def category(self) -> str:
        return self.kind

    @property
    def summary(self) -> str:
        """One-line Polish text ready for a notification."""
        parts: list[str] = []
        price = _pln(self.price) if _amount(self.price) is not None else None
        regular = _pln(self.regular_price)
        # Multi-buy deals ("DRUGI ZA 2 ZŁ", "2+1 GRATIS") carry the deal in text;
        # `price` is then the per-item price when buying the whole set.
        tag = self.price_tag_info or (self.price if price is None else None)
        line = self.name or "Oferta Shakeomatu"
        if tag:
            extras = []
            if price:
                note = (self.price_tag_note or "").strip().lower()
                extras.append(f"{price} {note}" if note else f"{price}/szt. w promocji")
            if regular and regular != price:
                extras.append(f"regularnie {regular}")
            line = f"{line}: {tag.strip().lower()}"
            if extras:
                line = f"{line} ({', '.join(extras)})"
        elif price:
            line = f"{line}: {price}"
            if regular and regular != price:
                line = f"{line} zamiast {regular}"
            discount = (self.discount or "").strip().lstrip("-−").rstrip("%")
            if discount:
                line = f"{line} (−{discount}%)"
        parts.append(f"{line}.")
        limit = self.details or self.limit_message_short
        if limit:
            parts.append(_sentence(limit))
        parts.append(_validity(self.valid_from, self.valid_to))
        return _without_card_name(" ".join(part for part in parts if part))

    def as_dict(self) -> dict[str, Any]:
        return {
            **{name: getattr(self, name) for name in _REWARD_FIELDS},
            "kind": self.kind,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShakeomatReward | None:
        offer_id = data.get("offer_id")
        if not offer_id:
            return None
        values = {name: data.get(name) for name in _REWARD_FIELDS}
        values["offer_id"] = str(offer_id)
        values["badges"] = [str(b) for b in data.get("badges") or [] if b]
        return cls(**values)


_REWARD_FIELDS = tuple(ShakeomatReward.__dataclass_fields__)
# Offer data copied from the dashboard into a reward that is missing it.
REWARD_DATA_FIELDS = tuple(
    name
    for name in _REWARD_FIELDS
    if name not in {"offer_id", "claimed_at", "source"}
)


def _amount(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _pln(value: str | None) -> str | None:
    """'2.99' -> '2,99 zł'; anything non-numeric is returned unchanged."""
    amount = _amount(value)
    if amount is None:
        return value or None
    return f"{amount:.2f}".replace(".", ",") + " zł"


_CARD_NAME = re.compile(r"\b(kart(?:ę|ą|a|y|ie)) Moja Biedronka\b")


def _without_card_name(text: str) -> str:
    """'na kartę Moja Biedronka' -> 'na kartę': in a Biedronka notification it is obvious."""
    return _CARD_NAME.sub(r"\1", text)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _validity(start: str | None, end: str | None) -> str:
    """'Ważne 12.10–17.10.' or 'Ważne do 13.10 23:59.' (empty when unknown)."""
    valid_from = _local_dt(start)
    valid_to = _local_dt(end)
    if valid_from and valid_to and valid_from.date() != valid_to.date():
        return f"Ważne {valid_from:%d.%m}–{valid_to:%d.%m}."
    if valid_to:
        return f"Ważne do {valid_to:%d.%m %H:%M}."
    return ""


def _local_dt(value: str | None) -> datetime | None:
    """ISO timestamp in Polish time."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_WARSAW)
    return parsed.astimezone(_WARSAW)


def badge_labels(badges: Any) -> list[str]:
    if not isinstance(badges, list):
        return []
    labels: list[str] = []
    for badge in badges:
        if not isinstance(badge, dict):
            continue
        label = badge.get("label")
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    return labels


@dataclass
class Voucher:
    """A coupon from promos/coupons/, shown in the app as a voucher."""

    kind: str | None = None
    number: str | None = None
    status: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    discount: str | None = None
    conditions: str | None = None
    burned_date: str | None = None
    burned_location: str | None = None
    badges: list[str] = field(default_factory=list)

    category = KIND_VOUCHER

    @property
    def key(self) -> str:
        return f"voucher_{self.number}"

    @property
    def summary(self) -> str:
        parts = [f"Voucher: {self.discount}." if self.discount else "Nowy voucher."]
        if self.conditions:
            parts.append(_sentence(self.conditions))
        parts.append(_validity(self.valid_from, self.valid_to))
        return _without_card_name(" ".join(part for part in parts if part))

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "kind": self.kind,
            "number": self.number,
            "status": self.status,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "discount": self.discount,
            "conditions": self.conditions,
            "burned_date": self.burned_date,
            "burned_location": self.burned_location,
            "badges": self.badges,
        }

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> Voucher:
        kind = item.get("meta")
        number = item.get("number")
        status = item.get("status")
        valid_from = item.get("valid_from")
        valid_to = item.get("valid_to")
        burned_date = item.get("burned_date")
        burned_location = item.get("burned_location")
        return cls(
            kind=kind.strip() if isinstance(kind, str) and kind.strip() else None,
            number=number.strip() if isinstance(number, str) and number.strip() else None,
            status=status.strip() if isinstance(status, str) and status.strip() else None,
            valid_from=valid_from if isinstance(valid_from, str) else None,
            valid_to=valid_to if isinstance(valid_to, str) else None,
            discount=localized_text(item.get("discount_description")),
            conditions=localized_text(item.get("usage_conditions")),
            burned_date=burned_date if isinstance(burned_date, str) else None,
            burned_location=(
                burned_location.strip()
                if isinstance(burned_location, str) and burned_location.strip()
                else None
            ),
            badges=badge_labels(item.get("badges")),
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
class LoyaltyAction:
    """A sticker / points campaign from loyalty-actions/, with its wallet balance."""

    action_id: str
    type: str | None = None
    status: str | None = None
    name: str | None = None
    headline: str | None = None
    collect_until: str | None = None
    end_on: str | None = None
    url: str | None = None
    image_url: str | None = None
    wallet_id: str | None = None
    balance: float | None = None
    rewards: list[dict[str, Any]] = field(default_factory=list)

    category = KIND_LOYALTY

    @property
    def key(self) -> str:
        return f"loyalty_{self.action_id}"

    @property
    def valid_to(self) -> str | None:
        return self.end_on

    @property
    def active(self) -> bool:
        return (self.status or "").lower() in LOYALTY_ACTIVE_STATUSES

    @property
    def unit(self) -> str | None:
        return _LOYALTY_UNITS.get((self.type or "").upper())

    @property
    def available_rewards(self) -> list[dict[str, Any]]:
        """Rewards the current balance already covers."""
        if self.balance is None:
            return []
        return [r for r in self.rewards if r.get("cost") is not None and r["cost"] <= self.balance]

    @property
    def next_reward(self) -> dict[str, Any] | None:
        """The cheapest reward still out of reach."""
        if self.balance is None:
            return None
        return next(
            (r for r in self.rewards if r.get("cost") is not None and r["cost"] > self.balance),
            None,
        )

    @property
    def summary(self) -> str:
        parts = [f"Nowa akcja: {self.name or 'Biedronka'}."]
        if self.headline:
            parts.append(_sentence(self.headline))
        collect = _local_dt(self.collect_until)
        end = _local_dt(self.end_on)
        if collect and end and collect.date() != end.date():
            parts.append(f"Zbieranie do {collect:%d.%m}, odbiór nagród do {end:%d.%m}.")
        elif end:
            parts.append(f"Trwa do {end:%d.%m}.")
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        next_reward = self.next_reward
        return {
            "action_id": self.action_id,
            "type": self.type,
            "status": self.status,
            "name": self.name,
            "headline": self.headline,
            "collect_until": self.collect_until,
            "end_on": self.end_on,
            "url": self.url,
            "image_url": self.image_url,
            "wallet_id": self.wallet_id,
            "balance": self.balance,
            "unit": self.unit,
            "rewards": self.rewards,
            "available_rewards": len(self.available_rewards),
            "next_reward": next_reward,
            "missing_to_next_reward": (
                next_reward["cost"] - self.balance
                if next_reward and self.balance is not None
                else None
            ),
            "summary": self.summary,
        }

    @classmethod
    def from_payload(
        cls, item: dict[str, Any], balances: dict[str, float]
    ) -> LoyaltyAction | None:
        action_id = item.get("id")
        if action_id in (None, ""):
            return None
        wallet_id = _str_or_none(item.get("wallet_first"))
        raw_name = _str_or_none(localized_text(item.get("name")))
        return cls(
            action_id=str(action_id),
            type=_str_or_none(item.get("type")),
            status=_str_or_none(item.get("status")),
            # `name` is an internal slug such as "Gang_Zaradniaków_Ongoing".
            name=_str_or_none(
                localized_text(item.get("popup_headline"))
                or localized_text(item.get("popup_award_tab1"))
            )
            or (raw_name.replace("_", " ") if raw_name else None),
            headline=_str_or_none(localized_text(item.get("widget_secondarytext"))),
            collect_until=_str_or_none(item.get("publication_end_date")),
            end_on=_str_or_none(item.get("end_on")),
            url=_str_or_none(item.get("biedronka_home_url")),
            image_url=_str_or_none(item.get("popup_loyalty_action_image")),
            wallet_id=wallet_id,
            balance=balances.get(wallet_id) if wallet_id else None,
            rewards=_loyalty_rewards(item),
        )


def _loyalty_rewards(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Rewards sorted by cost, from numbered `awardN_*` fields or reward lists."""
    rewards: list[dict[str, Any]] = []
    for n in range(1, _MAX_NUMBERED_AWARDS + 1):
        cost = item.get(f"award{n}_stickers_value")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            continue
        title = _str_or_none(item.get(f"popup_electronic_carrier_badge{n}")) or _str_or_none(
            item.get(f"award{n}_locked_text_accusative")
        )
        rewards.append(
            {
                "title": title,
                "cost": cost,
                "regular_price": _str_or_none(item.get(f"award_regular_price_{n}")),
                "image_url": _str_or_none(item.get(f"award{n}_icon")),
            }
        )
    for reward in [*(item.get("rewards") or []), *(item.get("awards") or [])]:
        if not isinstance(reward, dict):
            continue
        cost = reward.get("cost", reward.get("stickers_value"))
        rewards.append(
            {
                "title": _str_or_none(reward.get("title") or reward.get("name")),
                "cost": cost if isinstance(cost, (int, float)) else None,
                "regular_price": _str_or_none(reward.get("regular_price")),
                "image_url": _str_or_none(reward.get("thumbnail") or reward.get("icon")),
            }
        )
    return sorted(rewards, key=lambda r: (r["cost"] is None, r["cost"] or 0))


@dataclass
class AppMessage:
    """A message from the app's inbox (notifications/)."""

    message_id: str
    title: str | None = None
    preview: str | None = None
    message: str | None = None
    created_at: str | None = None
    image_url: str | None = None

    category = KIND_MESSAGE

    @property
    def key(self) -> str:
        return f"message_{self.message_id}"

    @property
    def valid_to(self) -> str | None:
        return None

    @property
    def summary(self) -> str:
        text = self.preview or self.message or ""
        if self.title and text:
            return f"{self.title}: {text}"
        return self.title or text or "Nowa wiadomość w aplikacji Biedronka."

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "title": self.title,
            "preview": self.preview,
            "message": self.message,
            "created_at": self.created_at,
            "image_url": self.image_url,
            "summary": self.summary,
        }

    @classmethod
    def from_payload(cls, item: Any) -> AppMessage | None:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            return None
        return cls(
            message_id=str(item["id"]),
            title=_str_or_none(localized_text(item.get("title"))),
            preview=_str_or_none(localized_text(item.get("message_preview"))),
            message=_str_or_none(localized_text(item.get("message"))),
            created_at=_str_or_none(item.get("created_at")),
            image_url=_str_or_none(item.get("main_image_url") or item.get("thumbnail_url")),
        )


def app_messages(payload: Any) -> list[AppMessage]:
    items = payload.get("notifications") if isinstance(payload, dict) else payload
    messages = (AppMessage.from_payload(item) for item in items or [])
    return sorted(
        (message for message in messages if message),
        key=lambda message: message.created_at or "",
        reverse=True,
    )


@dataclass
class WalletMove:
    """A sticker / points movement from the wallet history."""

    wallet_id: str
    action: str | None
    when: str | None
    delta: float
    balance: float | None = None
    label: str | None = None
    info: str | None = None
    unit: str | None = None
    ends: str | None = None

    category = KIND_STICKERS

    @property
    def key(self) -> str:
        return f"wallet_{self.wallet_id}_{self.when}_{self.delta:g}"

    @property
    def valid_to(self) -> str | None:
        # Forgotten once the campaign is over.
        return self.ends

    @property
    def summary(self) -> str:
        sign = "+" if self.delta > 0 else "−"
        line = sign + count_with_unit(abs(self.delta), self.unit)
        if self.action:
            line += f" ({self.action})"
        detail = self.label or self.info
        if detail:
            line += f": {detail}"
        if self.balance is not None:
            line += f". Masz teraz {self.balance:g}"
        return line + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "wallet_id": self.wallet_id,
            "action": self.action,
            "when": self.when,
            "delta": self.delta,
            "balance": self.balance,
            "label": self.label,
            "info": self.info,
            "summary": self.summary,
        }


def wallet_moves(
    payload: Any, wallet_id: str, action: LoyaltyAction | None
) -> list[WalletMove]:
    """Newest first; `balance` is filled for the newest move from the action."""
    items = payload.get("transactions") if isinstance(payload, dict) else None
    moves: list[WalletMove] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            delta = float(item.get("balance_delta") or 0)
        except (TypeError, ValueError):
            continue
        if not delta:
            continue
        moves.append(
            WalletMove(
                wallet_id=wallet_id,
                action=action.name if action else None,
                when=_str_or_none(item.get("transaction_datetime")),
                delta=delta,
                label=_str_or_none(localized_text(item.get("operation_type_label"))),
                info=_str_or_none(localized_text(item.get("additional_info"))),
                unit=action.unit if action else None,
                ends=action.end_on if action else None,
            )
        )
    moves.sort(key=lambda move: move.when or "", reverse=True)
    if moves and action is not None:
        moves[0].balance = action.balance
    return moves


@dataclass
class RewardReady:
    """A loyalty reward the current balance now covers."""

    action: LoyaltyAction
    reward: dict[str, Any]

    category = KIND_REWARD

    @property
    def key(self) -> str:
        return f"reward_{self.action.action_id}_{self.reward.get('cost')}"

    @property
    def valid_to(self) -> str | None:
        return self.action.end_on

    @property
    def summary(self) -> str:
        unit = self.action.unit
        balance = self.action.balance
        cost = self.reward.get("cost")
        line = f"{self.action.name or 'Akcja Biedronki'}: "
        if balance is not None:
            line += f"masz {count_with_unit(balance, unit)} — "
        line += f"możesz odebrać {self.reward.get('title') or 'nagrodę'}"
        if cost is not None:
            line += f" ({count_with_unit(cost, unit)})"
        return line + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.name,
            "balance": self.action.balance,
            "reward": self.reward,
            "image_url": self.reward.get("image_url"),
            "summary": self.summary,
        }


def rewards_ready(action: LoyaltyAction) -> list[RewardReady]:
    return [RewardReady(action, reward) for reward in action.available_rewards]


def wallet_balances(payload: Any) -> dict[str, float]:
    """users/me/wallets/ -> {perspectiv_id: balance}."""
    balances: dict[str, float] = {}
    for wallet in payload if isinstance(payload, list) else []:
        if not isinstance(wallet, dict) or wallet.get("perspectiv_id") in (None, ""):
            continue
        try:
            balances[str(wallet["perspectiv_id"])] = float(wallet.get("balance") or 0)
        except (TypeError, ValueError):
            continue
    return balances


def count_with_unit(count: float, unit: str | None) -> str:
    """'10 naklejek', '2 naklejki', '1 punkt' — Polish plural of the campaign unit."""
    one, few, many = _UNIT_FORMS.get(unit or "naklejki", (unit, unit, unit))
    if count != int(count):
        form = few
    elif count == 1:
        form = one
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        form = few
    else:
        form = many
    return f"{count:g} {form}"


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass
class BiedronkaStore:
    """The store chosen in the app (`store` in users/me/), with its opening hours."""

    code: str
    name: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    # [{"date": "2026-10-13", "opening": "06:00", "closing": "22:00", "closed": False}]
    days: list[dict[str, Any]] = field(default_factory=list)
    closed_now: bool | None = None
    target_hour: str | None = None
    refurbishment: bool = False

    @property
    def address(self) -> str | None:
        """'Warszawa, ul. Przykładowa 1' — like the app."""
        parts = [part for part in (self.city, self.street) if part]
        return ", ".join(parts) or None

    def day(self, date: str) -> dict[str, Any] | None:
        return next((day for day in self.days if day["date"] == date), None)

    def hours(self, date: str) -> str | None:
        """'06:00–22:00', or None when closed (or unknown) that day."""
        day = self.day(date)
        if not day or day["closed"] or not day["opening"] or not day["closing"]:
            return None
        return f"{day['opening']}–{day['closing']}"

    def open_at(self, now: datetime) -> bool | None:
        """Open right now, from today's hours; the API flag is only as fresh as the poll."""
        if self.refurbishment:
            return False
        local = now.astimezone(_WARSAW)
        day = self.day(local.date().isoformat())
        if day is None:
            return None if self.closed_now is None else not self.closed_now
        if day["closed"] or not day["opening"] or not day["closing"]:
            return False
        clock = local.strftime("%H:%M")
        return day["opening"] <= clock < day["closing"]

    def as_dict(self, today: str) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "address": self.address,
            "zip_code": self.zip_code,
            "today": self.hours(today),
            "closed_today": self.hours(today) is None,
            "in_refurbishment": self.refurbishment,
            "target_hour": self.target_hour,
            "days": [
                {"date": day["date"], "hours": self.hours(day["date"])}
                for day in self.days
            ],
        }

    @classmethod
    def from_payload(cls, store: Any) -> BiedronkaStore | None:
        if not isinstance(store, dict) or not store.get("code"):
            return None
        days = []
        for item in store.get("opening_hours") or []:
            if not isinstance(item, dict) or not item.get("date"):
                continue
            days.append(
                {
                    "date": str(item["date"])[:10],
                    "opening": _hhmm(item.get("opening_time")),
                    "closing": _hhmm(item.get("closing_time")),
                    "closed": item.get("store_is_closed") is True,
                }
            )
        closed_now = store.get("is_closed_now")
        return cls(
            code=str(store["code"]),
            name=_str_or_none(store.get("name")),
            street=_str_or_none(store.get("street")),
            zip_code=_str_or_none(store.get("zip_code")),
            city=_str_or_none(store.get("city")),
            days=days,
            closed_now=closed_now if isinstance(closed_now, bool) else None,
            target_hour=_str_or_none(store.get("target_hour")),
            refurbishment=store.get("is_in_refurbishment") is True,
        )


def _hhmm(value: Any) -> str | None:
    """'06:00:00' -> '06:00'."""
    if not isinstance(value, str) or len(value) < 5:
        return None
    return value[:5]


@dataclass
class BiedronkaData:
    user: dict[str, Any] = field(default_factory=dict)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    last_transaction: dict[str, Any] | None = None
    last_details: dict[str, Any] | None = None
    today_total: float = 0.0
    receipts: list[Receipt] = field(default_factory=list)
    vouchers: list[Voucher] = field(default_factory=list)
    shakeomats: list[ShakeomatOffer] = field(default_factory=list)
    current_shakeomat: ShakeomatOffer | None = None
    available_shakeomats: list[ShakeomatOffer] = field(default_factory=list)
    last_reward: ShakeomatReward | None = None
    reward_history: list[ShakeomatReward] = field(default_factory=list)
    card_offers: list[ShakeomatReward] = field(default_factory=list)
    loyalty_actions: list[LoyaltyAction] = field(default_factory=list)
    messages: list[AppMessage] = field(default_factory=list)
    wallet_moves: list[WalletMove] = field(default_factory=list)
    # Offers, vouchers and actions first seen in this refresh; event entities
    # announce them. Each has `category`, `key`, `valid_to` and `as_dict()`.
    new_offers: list[Any] = field(default_factory=list)
    # Sections with a correct response, or still inside 24h of the last one.
    # Anything missing here is unknown until the API answers properly again.
    known: set[str] = field(default_factory=set)

    @property
    def store(self) -> BiedronkaStore | None:
        """The store chosen in the app, from the profile already fetched."""
        return BiedronkaStore.from_payload(self.user.get("store"))

    @property
    def active_loyalty_actions(self) -> list[LoyaltyAction]:
        return [action for action in self.loyalty_actions if action.active]

    def shakeomats_of(self, kind: str) -> list[ShakeomatOffer]:
        return [offer for offer in self.shakeomats if offer.kind == kind]

    def current_of(self, kind: str) -> ShakeomatOffer | None:
        """The card to shake now, else the next one still cooling down."""
        offers = self.shakeomats_of(kind)
        return next(
            (offer for offer in offers if offer.status == STATUS_AVAILABLE),
            next((offer for offer in offers if offer.status == STATUS_COOLDOWN), None),
        )

    def rewards_of(self, kind: str) -> list[ShakeomatReward]:
        return [reward for reward in self.reward_history if reward.kind == kind]
