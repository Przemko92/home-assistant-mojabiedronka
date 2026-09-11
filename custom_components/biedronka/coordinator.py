"""DataUpdateCoordinator for Biedronka."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BiedronkaApi
from .const import (
    CONF_AUTO_SHAKEOMAT,
    CONF_SCAN_INTERVAL,
    DEFAULT_AUTO_SHAKEOMAT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_RECEIPTS,
    MAX_REWARD_HISTORY,
    SHAKEOMAT_TYPES,
    STORAGE_VERSION,
)
from .exceptions import BiedronkaAuthError, BiedronkaCannotConnect, BiedronkaError
from .models import (
    RECEIPT_SOURCE_DETAILS,
    RECEIPT_SOURCE_E_RECEIPT,
    STATUS_AVAILABLE,
    STATUS_CLAIMED,
    STATUS_COOLDOWN,
    STATUS_EXPIRED,
    BiedronkaData,
    Receipt,
    ShakeomatOffer,
    ShakeomatReward,
    slim_receipt_lines,
)

_LOGGER = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
ASSETS_TTL = timedelta(hours=24)
EPOCH = datetime.min.replace(tzinfo=WARSAW)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed


def _as_sections(dashboard: Any) -> list[dict[str, Any]]:
    if isinstance(dashboard, list):
        return [item for item in dashboard if isinstance(item, dict)]
    if isinstance(dashboard, dict):
        sections = dashboard.get("sections") or []
        return [item for item in sections if isinstance(item, dict)]
    return []


def _text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _reward_from_payload(offer_id: str, payload: dict[str, Any]) -> ShakeomatReward:
    """Map the revealed offer (ACTION or SINGLE_PRODUCT_PROMOTION) to a reward."""
    return ShakeomatReward(
        offer_id=offer_id,
        claimed_at=datetime.now(WARSAW).isoformat(timespec="seconds"),
        name=_text(payload, "name", "title"),
        details=_text(payload, "details", "limit_message"),
        description=_text(payload, "description"),
        price=_text(payload, "price", "price_tag_info"),
        discount=_text(payload, "discount"),
        image_url=_text(payload, "image_url"),
        valid_from=_text(payload, "start", "start_date"),
        valid_to=_text(payload, "end", "end_date"),
    )


class BiedronkaCoordinator(DataUpdateCoordinator[BiedronkaData]):
    """Poll user, transactions and Shakeomat state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: BiedronkaApi) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL)
        update_interval = (
            timedelta(minutes=int(interval)) if interval else DEFAULT_SCAN_INTERVAL
        )
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self.entry = entry
        self.api = api
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.shakeomat"
        )
        self._rewards: dict[str, ShakeomatReward] = {}
        self._rewards_loaded = False
        self._receipts: dict[str, Receipt] = {}
        self._assets: dict[str, str] = {}
        self._assets_fetched: datetime | None = None

    @property
    def auto_shakeomat(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_SHAKEOMAT, DEFAULT_AUTO_SHAKEOMAT))

    async def _async_update_data(self) -> BiedronkaData:
        await self.async_load_rewards()
        try:
            user = await self.api.users_me()
            tx_page = await self.api.transactions(page=1)
        except BiedronkaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except BiedronkaCannotConnect as err:
            raise UpdateFailed(str(err)) from err
        except BiedronkaError as err:
            raise UpdateFailed(str(err)) from err

        transactions = list(tx_page.get("transactions") or []) if isinstance(tx_page, dict) else []
        last = transactions[0] if transactions else None
        receipts = await self._sync_receipts(transactions)
        details = (
            receipts[0].payload
            if receipts and isinstance(receipts[0].payload, dict)
            else None
        )

        today_total = await self._today_total(transactions, tx_page if isinstance(tx_page, dict) else {})
        shakeomats = await self._load_shakeomats()

        if self.auto_shakeomat:
            available = [offer for offer in shakeomats if offer.status == STATUS_AVAILABLE]
            if await self._activate_offers(available, refresh=False):
                shakeomats = await self._load_shakeomats()

        data = BiedronkaData(
            user=user if isinstance(user, dict) else {},
            transactions=transactions,
            last_transaction=last,
            last_details=details,
            today_total=today_total,
            receipts=receipts,
        )
        self._apply_shakeomats(data, shakeomats)
        return data

    async def _today_total(
        self, first_page: list[dict[str, Any]], page_meta: dict[str, Any]
    ) -> float:
        today = datetime.now(WARSAW).date()
        total = 0.0
        pages_seen = {1}
        items = list(first_page)
        page = 1
        page_count = int(page_meta.get("page_count") or 1)
        while True:
            more_today = False
            for tx in items:
                parsed = _parse_dt(tx.get("date"))
                if parsed is None:
                    continue
                if parsed.astimezone(WARSAW).date() == today:
                    total += float(tx.get("total_price") or 0)
                    more_today = True
            next_page = page_meta.get("next_page")
            if not more_today or not next_page or page >= page_count:
                break
            page = int(next_page)
            if page in pages_seen:
                break
            pages_seen.add(page)
            try:
                page_meta = await self.api.transactions(page=page)
            except BiedronkaError:
                break
            items = list(page_meta.get("transactions") or [])
        return round(total, 2)

    def _apply_shakeomats(self, data: BiedronkaData, offers: list[ShakeomatOffer]) -> None:
        available = [offer for offer in offers if offer.status == STATUS_AVAILABLE]
        current = available[0] if available else None
        if current is None:
            current = next(
                (offer for offer in offers if offer.status == STATUS_COOLDOWN), None
            )
        history = list(reversed(self._rewards.values()))
        data.shakeomats = offers
        data.available_shakeomats = available
        data.current_shakeomat = current
        data.reward_history = history
        data.last_reward = history[0] if history else None

    async def _load_shakeomats(self) -> list[ShakeomatOffer]:
        """Collect every Shakeomat card the dashboard exposes, however many there are."""
        try:
            dashboard = await self.api.dashboard()
        except BiedronkaError as err:
            _LOGGER.debug("Dashboard failed: %s", err)
            return []

        assets = await self._load_assets()
        now = datetime.now(WARSAW)
        offers: dict[str, ShakeomatOffer] = {}
        for section in _as_sections(dashboard):
            if str(section.get("type") or "").upper() != "CAROUSELS":
                continue
            param = section.get("param")
            if not param:
                continue
            try:
                carousel = await self.api.carousel(str(param))
            except BiedronkaError as err:
                _LOGGER.debug("Carousel %s failed: %s", param, err)
                continue
            items = carousel.get("items") if isinstance(carousel, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").upper() not in SHAKEOMAT_TYPES:
                    continue
                offer = self._classify(item, now, assets)
                key = offer.offer_id or f"anonymous_{len(offers)}"
                offers.setdefault(key, offer)

        return sorted(
            offers.values(), key=lambda offer: _parse_dt(offer.available_from) or EPOCH
        )

    def _classify(
        self, item: dict[str, Any], now: datetime, assets: dict[str, str]
    ) -> ShakeomatOffer:
        raw_id = item.get("offer_id") or item.get("id")
        offer_id = str(raw_id) if raw_id else None
        assets_id = item.get("meta")
        start = _parse_dt(item.get("available_from"))
        end = _parse_dt(item.get("available_to"))
        if offer_id and offer_id in self._rewards:
            status = STATUS_CLAIMED
        elif start and start > now:
            status = STATUS_COOLDOWN
        elif end and end <= now:
            status = STATUS_EXPIRED
        else:
            status = STATUS_AVAILABLE
        return ShakeomatOffer(
            offer_id=offer_id,
            status=status,
            item_type=str(item.get("type")) if item.get("type") else None,
            offer_type=item.get("offer_type"),
            available_from=item.get("available_from"),
            available_to=item.get("available_to"),
            assets_id=str(assets_id) if assets_id else None,
            assets_name=assets.get(str(assets_id)) if assets_id else None,
        )

    async def _load_assets(self) -> dict[str, str]:
        """Names of the Shakeomat variants, matched to the item `meta` field."""
        now = datetime.now(WARSAW)
        if self._assets_fetched and now - self._assets_fetched < ASSETS_TTL:
            return self._assets
        try:
            payload = await self.api.shakeomat_assets()
        except BiedronkaError as err:
            _LOGGER.debug("Shakeomat assets failed: %s", err)
            return self._assets
        assets: dict[str, str] = {}
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                assets[str(item["id"])] = name.strip()
        self._assets = assets
        self._assets_fetched = now
        return assets

    async def async_load_rewards(self) -> None:
        if self._rewards_loaded:
            return
        self._rewards_loaded = True
        stored = await self._store.async_load()
        entries = stored.get("rewards") if isinstance(stored, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            reward = ShakeomatReward.from_dict(entry)
            if reward:
                self._rewards[reward.offer_id] = reward
        self._prune_rewards()

    async def _sync_receipts(self, transactions: list[dict[str, Any]]) -> list[Receipt]:
        """Download JSON for the newest receipts, skipping IDs already fetched this session."""
        wanted = [tx for tx in transactions if isinstance(tx, dict) and tx.get("id")][:MAX_RECEIPTS]
        ordered: dict[str, Receipt] = {}
        for tx in wanted:
            tx_id = str(tx["id"])
            cached = self._receipts.get(tx_id)
            if cached:
                ordered[tx_id] = cached
                continue
            receipt = await self._fetch_receipt(tx)
            if receipt is None:
                continue
            ordered[tx_id] = receipt
        self._receipts = ordered
        return list(self._receipts.values())

    async def _fetch_receipt(self, tx: dict[str, Any]) -> Receipt | None:
        tx_id = str(tx["id"])
        try:
            if tx.get("is_e_receipt_available"):
                payload = await self.api.e_receipt(tx_id)
                source = RECEIPT_SOURCE_E_RECEIPT
            else:
                payload = await self.api.transaction_details(tx_id)
                source = RECEIPT_SOURCE_DETAILS
        except BiedronkaError as err:
            _LOGGER.debug("Receipt %s failed: %s", tx_id, err)
            return None
        if payload is None:
            return None
        details = payload if isinstance(payload, dict) else {}
        total = tx.get("total_price")
        if total is None:
            total = details.get("total_price")
        try:
            total_price = float(total) if total is not None else None
        except (TypeError, ValueError):
            total_price = None
        return Receipt(
            id=tx_id,
            date=tx.get("date") or details.get("date"),
            store_name=tx.get("store_name") or details.get("store_name"),
            receipt_num=tx.get("receipt_num") or details.get("receipt_num"),
            total_price=total_price,
            source=source,
            lines=slim_receipt_lines(payload),
            payload=payload if source == RECEIPT_SOURCE_DETAILS else None,
        )

    def _prune_rewards(self) -> None:
        now = datetime.now(WARSAW)
        for offer_id, reward in list(self._rewards.items()):
            end = _parse_dt(reward.valid_to)
            if end and end < now:
                del self._rewards[offer_id]
        while len(self._rewards) > MAX_REWARD_HISTORY:
            self._rewards.pop(next(iter(self._rewards)))

    async def _save_rewards(self) -> None:
        await self._store.async_save(
            {"rewards": [reward.as_dict() for reward in self._rewards.values()]}
        )

    async def async_activate_all(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        """Reveal every offer that is ready right now, no matter how many there are."""
        await self.async_load_rewards()
        offers = list(self.data.available_shakeomats) if self.data else []
        if not offers:
            offers = [
                offer
                for offer in await self._load_shakeomats()
                if offer.status == STATUS_AVAILABLE
            ]
        return await self._activate_offers(offers, refresh=refresh)

    async def _activate_offers(
        self, offers: list[ShakeomatOffer], *, refresh: bool
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        for offer in offers:
            if not offer.offer_id or offer.offer_id in self._rewards:
                continue
            try:
                claimed.append(
                    await self.async_activate_shakeomat(offer.offer_id, refresh=False)
                )
            except BiedronkaError as err:
                _LOGGER.warning("Shakeomat %s activation failed: %s", offer.offer_id, err)
        if claimed and refresh:
            await self.async_request_refresh()
        return claimed

    async def async_activate_shakeomat(
        self, offer_id: str, *, refresh: bool = True
    ) -> dict[str, Any]:
        await self.async_load_rewards()
        result = await self.api.reveal_and_activate(offer_id)
        reward = _reward_from_payload(offer_id, result if isinstance(result, dict) else {})
        self._rewards.pop(offer_id, None)
        self._rewards[offer_id] = reward
        self._prune_rewards()
        await self._save_rewards()
        self._notify(reward)
        if refresh:
            await self.async_request_refresh()
        return reward.as_dict()

    def _notify(self, reward: ShakeomatReward) -> None:
        from homeassistant.components import persistent_notification

        title = reward.name or "Shakeomat"
        extra = [value for value in (reward.price, reward.discount) if value]
        message = title if not extra else f"{title} ({', '.join(extra)})"
        persistent_notification.async_create(
            self.hass,
            message,
            title="Biedronka Shakeomat",
            notification_id=f"biedronka_shakeomat_{reward.offer_id}",
        )
