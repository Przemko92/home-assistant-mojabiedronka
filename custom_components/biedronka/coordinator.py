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
from .exceptions import BiedronkaAuthError, BiedronkaError
from .models import (
    EXPIRING_VOUCHER_STATUSES,
    KIND_CARD,
    KIND_LOYALTY,
    KIND_MESSAGE,
    KIND_REWARD,
    KIND_SHAKEOMARKA,
    KIND_SHAKEOMAT,
    KIND_STICKERS,
    KIND_VOUCHER,
    OFFER_KINDS,
    RECEIPT_SOURCE_DETAILS,
    RECEIPT_SOURCE_E_RECEIPT,
    REWARD_DATA_FIELDS,
    REWARD_SOURCE_APP,
    REWARD_SOURCE_HA,
    REWARD_SOURCE_PERSONAL,
    STATUS_AVAILABLE,
    STATUS_CLAIMED,
    STATUS_COOLDOWN,
    STATUS_EXPIRED,
    AppMessage,
    BiedronkaData,
    LoyaltyAction,
    Receipt,
    RewardReady,
    ShakeomatOffer,
    ShakeomatReward,
    Voucher,
    WalletMove,
    app_messages,
    badge_labels,
    rewards_ready,
    slim_receipt_lines,
    wallet_balances,
    wallet_moves,
)

_LOGGER = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
ASSETS_TTL = timedelta(hours=24)
# A failed poll keeps the previous result. Only a gap longer than this
# turns the entity unknown.
STALE_AFTER = timedelta(hours=24)
EPOCH = datetime.min.replace(tzinfo=WARSAW)
MAX_WALLET_MOVES = 10
HISTORY_RETRY = timedelta(days=1)

SECTION_USER = "user"
SECTION_TRANSACTIONS = "transactions"
SECTION_VOUCHERS = "vouchers"
SECTION_SHAKEOMATS = "shakeomats"
SECTION_LOYALTY = "loyalty"
SECTION_MESSAGES = "messages"

_SECTION_KINDS = {
    SECTION_VOUCHERS: (KIND_VOUCHER,),
    SECTION_SHAKEOMATS: (KIND_SHAKEOMAT, KIND_SHAKEOMARKA, KIND_CARD),
    SECTION_LOYALTY: (KIND_LOYALTY, KIND_STICKERS, KIND_REWARD),
    SECTION_MESSAGES: (KIND_MESSAGE,),
}


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


def as_sections(dashboard: Any) -> list[dict[str, Any]]:
    if isinstance(dashboard, list):
        return [item for item in dashboard if isinstance(item, dict)]
    if isinstance(dashboard, dict):
        sections = dashboard.get("sections") or []
        return [item for item in sections if isinstance(item, dict)]
    return []


def nearest_expiring_voucher(vouchers: list[Voucher], now: datetime) -> Voucher | None:
    """Earliest still-valid ready or pending voucher."""
    chosen: tuple[datetime, Voucher] | None = None
    for voucher in vouchers:
        if voucher.status not in EXPIRING_VOUCHER_STATUSES:
            continue
        end = _parse_dt(voucher.valid_to)
        if end is None or end <= now:
            continue
        if chosen is None or end < chosen[0]:
            chosen = (end, voucher)
    return chosen[1] if chosen else None


def _text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


PERSONAL_CAROUSEL_TYPES = frozenset({"j4u"})
PROMOTION_ITEM_TYPES = frozenset({"SINGLE_PRODUCT_PROMOTION", "ACTION"})


def _is_personal_offer(carousel: dict[str, Any], item: dict[str, Any]) -> bool:
    """A "Tylko dla Ciebie" offer Biedronka assigned to this card (e.g. WELCOME)."""
    return (
        str(carousel.get("type") or "").lower() in PERSONAL_CAROUSEL_TYPES
        and str(item.get("type") or "").upper() in PROMOTION_ITEM_TYPES
    )


def _reward_from_payload(
    offer_id: str, payload: dict[str, Any], source: str = REWARD_SOURCE_HA
) -> ShakeomatReward:
    """Map the revealed offer (ACTION or SINGLE_PRODUCT_PROMOTION) to a reward."""
    return ShakeomatReward(
        offer_id=offer_id,
        promo_id=_text(payload, "id"),
        source=source,
        claimed_at=datetime.now(WARSAW).isoformat(timespec="seconds"),
        name=_text(payload, "name", "title"),
        details=_text(payload, "details", "limit_message"),
        description=_text(payload, "description"),
        price=_text(payload, "price"),
        discount=_text(payload, "discount"),
        image_url=_text(payload, "image_url"),
        valid_from=_text(payload, "start", "start_date"),
        valid_to=_text(payload, "end", "end_date"),
        regular_price=_text(payload, "regular_price"),
        unit=_text(payload, "unit"),
        unit_price=_text(payload, "unit_price"),
        regular_unit_price=_text(payload, "regular_unit_price"),
        omnibus_price=_text(payload, "omnibus_price"),
        discount_to_omnibus_price=_text(payload, "discount_to_omnibus_price"),
        price_tag_info=_text(payload, "price_tag_info"),
        price_tag_note=_text(payload, "price_tag_note"),
        limit_message_short=_text(payload, "limit_message_short"),
        regular_price_line=_text(payload, "regular_price_line"),
        omnibus_price_line=_text(payload, "omnibus_price_line"),
        deposit=_text(payload, "deposit"),
        origin_country=_text(payload, "origin_country"),
        ean=_text(payload, "product_main_ean_code"),
        offer_type=_text(payload, "offer_type", "type"),
        promotion_type=_text(payload, "promotion_type"),
        badges=badge_labels(payload.get("badges")),
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
        self._card_offers: list[ShakeomatReward] = []
        # Offer key -> valid_to, for offers already announced as new.
        self._notified: dict[str, str | None] = {}
        # Categories already tracked; the first refresh of a new one is silent.
        self._notified_kinds: set[str] = set()
        # Wallet -> last seen campaign balance, for the "stickers changed" event.
        self._balances: dict[str, float] = {}
        # Wallet -> when its history last failed (sticker wallets have none).
        self._history_failed: dict[str, datetime] = {}
        self._receipts: dict[str, Receipt] = {}
        self._assets: dict[str, str] = {}
        self._assets_fetched: datetime | None = None
        # section -> (when it last succeeded, value)
        self._fresh: dict[str, tuple[datetime, Any]] = {}
        self._stale_logged: set[str] = set()
        self._succeeded = False
        self._failure: str | None = None

    @property
    def auto_shakeomat(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_SHAKEOMAT, DEFAULT_AUTO_SHAKEOMAT))

    def _keep(self, section: str, value: Any, *, failed: bool) -> tuple[Any, bool]:
        """Store a correct response, or reuse it until a newer one arrives.

        A failed response keeps the previous value for 24 hours. After that
        the section is unknown. Returns ``(value, known)``.
        """
        now = datetime.now(WARSAW)
        if not failed:
            self._fresh[section] = (now, value)
            self._succeeded = True
            self._stale_logged.discard(section)
            return value, True
        kept = self._fresh.get(section)
        if kept is not None and now - kept[0] <= STALE_AFTER:
            _LOGGER.debug(
                "Keeping the last %s result; the API response was not usable",
                section,
            )
            return kept[1], True
        if kept is not None and section not in self._stale_logged:
            self._stale_logged.add(section)
            _LOGGER.warning(
                "No correct Biedronka response for %s for over 24h; showing unknown",
                section,
            )
        return None, False

    def _unknown(self, section: str, unloaded: set[str]) -> None:
        unloaded.update(_SECTION_KINDS.get(section, ()))

    async def _async_update_data(self) -> BiedronkaData:
        await self.async_load_rewards()
        try:
            data = await self._fetch_data()
        except BiedronkaAuthError as err:
            raise ConfigEntryAuthFailed from err
        # Never succeeded at all: let Home Assistant retry the first refresh.
        # After a real result, keep publishing — stale sections are unknown
        # instead of making every entity unavailable.
        if data.known or self._succeeded:
            return data
        raise UpdateFailed(self._failure or "Biedronka API returned no data")

    async def _fetch_data(self) -> BiedronkaData:
        # A category whose first load failed must not be marked as seen, or
        # everything already there would be announced once it loads.
        unloaded: set[str] = set()
        user, user_known = await self._fetch_user()
        transactions, tx_known = await self._fetch_transactions()
        vouchers, vouchers_known = await self._fetch_vouchers(unloaded)
        offers, shake_known = await self._fetch_shakeomats(unloaded)
        actions, moves, loyalty_known = await self._fetch_loyalty(unloaded)
        messages, messages_known = await self._fetch_messages(unloaded)

        data = BiedronkaData(
            user=user,
            transactions=transactions["transactions"],
            last_transaction=transactions["last"],
            last_details=transactions["details"],
            today_total=transactions["today"],
            receipts=transactions["receipts"],
            vouchers=vouchers,
            loyalty_actions=actions,
            messages=messages,
            wallet_moves=moves,
        )
        self._apply_shakeomats(data, offers)
        data.known = {
            section
            for section, known in (
                (SECTION_USER, user_known),
                (SECTION_TRANSACTIONS, tx_known),
                (SECTION_VOUCHERS, vouchers_known),
                (SECTION_SHAKEOMATS, shake_known),
                (SECTION_LOYALTY, loyalty_known),
                (SECTION_MESSAGES, messages_known),
            )
            if known
        }
        if loyalty_known:
            balance_moves = await self._balance_moves(data.active_loyalty_actions)
            ready = [
                reward
                for action in data.active_loyalty_actions
                for reward in rewards_ready(action)
            ]
            rearmed = self._rearm_rewards(data.active_loyalty_actions)
        else:
            balance_moves = []
            ready = []
            rearmed = False
        data.new_offers = await self._find_new_offers(
            [
                *data.messages,
                *balance_moves,
                *ready,
                *self._rewards.values(),
                *self._card_offers,
                *(
                    voucher
                    for voucher in vouchers
                    if voucher.number and voucher.status in EXPIRING_VOUCHER_STATUSES
                ),
                *data.active_loyalty_actions,
            ],
            unloaded,
        )
        # A claim drops the balance under a reward and forgets its key. That
        # has to be stored even when this refresh had nothing new to announce,
        # or a restart would keep the old key and stay silent the next time.
        if rearmed and not data.new_offers:
            await self._save_rewards()
        return data

    async def _fetch_user(self) -> tuple[dict[str, Any], bool]:
        failed = False
        try:
            user = await self.api.users_me()
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            self._failure = str(err)
            _LOGGER.debug("Profile failed: %s", err)
            user = None
            failed = True
        else:
            if not isinstance(user, dict):
                _LOGGER.debug("Profile payload was not usable")
                user = None
                failed = True
        kept, known = self._keep(SECTION_USER, user, failed=failed)
        return (kept if known else {}), known

    async def _fetch_transactions(self) -> tuple[dict[str, Any], bool]:
        failed = False
        try:
            tx_page = await self.api.transactions(page=1)
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            self._failure = str(err)
            _LOGGER.debug("Transactions failed: %s", err)
            tx_page = None
            failed = True
        else:
            if not isinstance(tx_page, dict):
                _LOGGER.debug("Transactions payload was not usable")
                tx_page = None
                failed = True
        if not failed:
            assert isinstance(tx_page, dict)
            transactions = list(tx_page.get("transactions") or [])
            last = transactions[0] if transactions else None
            receipts = await self._sync_receipts(transactions)
            details = (
                receipts[0].payload
                if receipts and isinstance(receipts[0].payload, dict)
                else None
            )
            today_total = await self._today_total(transactions, tx_page)
            bundle = {
                "transactions": transactions,
                "last": last,
                "details": details,
                "today": today_total,
                "receipts": receipts,
            }
            kept, known = self._keep(SECTION_TRANSACTIONS, bundle, failed=False)
        else:
            kept, known = self._keep(SECTION_TRANSACTIONS, None, failed=True)
        if not known:
            kept = {
                "transactions": [],
                "last": None,
                "details": None,
                "today": 0.0,
                "receipts": [],
            }
        return kept, known

    async def _fetch_vouchers(self, unloaded: set[str]) -> tuple[list[Voucher], bool]:
        loaded = await self._load_vouchers()
        kept, known = self._keep(SECTION_VOUCHERS, loaded, failed=loaded is None)
        if not known:
            self._unknown(SECTION_VOUCHERS, unloaded)
            return [], False
        return kept, True

    async def _fetch_shakeomats(
        self, unloaded: set[str]
    ) -> tuple[list[ShakeomatOffer], bool]:
        offers = await self._load_shakeomats()
        if offers is not None and self.auto_shakeomat:
            available = [offer for offer in offers if offer.status == STATUS_AVAILABLE]
            if await self._activate_offers(available, refresh=False):
                refreshed = await self._load_shakeomats()
                if refreshed is not None:
                    offers = refreshed
        if offers is not None:
            payload = {"offers": offers, "card_offers": list(self._card_offers)}
            kept, known = self._keep(SECTION_SHAKEOMATS, payload, failed=False)
        else:
            kept, known = self._keep(SECTION_SHAKEOMATS, None, failed=True)
        if not known:
            self._card_offers = []
            self._unknown(SECTION_SHAKEOMATS, unloaded)
            return [], False
        self._card_offers = list(kept["card_offers"])
        return kept["offers"], True

    async def _fetch_loyalty(
        self, unloaded: set[str]
    ) -> tuple[list[LoyaltyAction], list[WalletMove], bool]:
        actions = await self._load_loyalty_actions()
        if actions is not None:
            active = [action for action in actions if action.active]
            moves = await self._load_wallet_moves(active)
            payload = {"actions": actions, "moves": moves}
            kept, known = self._keep(SECTION_LOYALTY, payload, failed=False)
        else:
            kept, known = self._keep(SECTION_LOYALTY, None, failed=True)
        if not known:
            self._unknown(SECTION_LOYALTY, unloaded)
            return [], [], False
        return kept["actions"], kept["moves"], True

    async def _fetch_messages(
        self, unloaded: set[str]
    ) -> tuple[list[AppMessage], bool]:
        loaded = await self._load_messages()
        kept, known = self._keep(SECTION_MESSAGES, loaded, failed=loaded is None)
        if not known:
            self._unknown(SECTION_MESSAGES, unloaded)
            return [], False
        return kept, True

    async def _find_new_offers(
        self, offers: list[Any], unloaded: set[str] | None = None
    ) -> list[Any]:
        """Offers, vouchers and actions not announced before.

        The first refresh of every category only takes note of what is already
        there, so installing (or upgrading) does not flood you with notifications.
        """
        now = datetime.now(WARSAW)
        for key, valid_to in list(self._notified.items()):
            end = _parse_dt(valid_to)
            if end and end < now - timedelta(days=1):
                del self._notified[key]
        new = [offer for offer in offers if offer.key not in self._notified]
        seen_kinds = set(OFFER_KINDS) - (unloaded or set())
        silent = seen_kinds - self._notified_kinds
        if not new and not silent:
            return []
        for offer in new:
            self._notified[offer.key] = offer.valid_to
        self._notified_kinds.update(seen_kinds)
        await self._save_rewards()
        return [offer for offer in new if offer.category not in silent]

    async def _load_loyalty_actions(self) -> list[LoyaltyAction] | None:
        """Sticker / points campaigns with their wallet balance.

        None when the actions or the wallets could not be loaded. An empty
        list is a real response (no campaigns).
        """
        try:
            payload = await self.api.loyalty_actions()
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            _LOGGER.debug("Loyalty actions failed: %s", err)
            return None
        try:
            balances = wallet_balances(await self.api.wallets())
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            _LOGGER.debug("Wallets failed: %s", err)
            return None
        actions = (
            LoyaltyAction.from_payload(item, balances)
            for item in (payload if isinstance(payload, list) else [])
            if isinstance(item, dict)
        )
        return [action for action in actions if action is not None]

    async def _load_messages(self) -> list[AppMessage] | None:
        """The app's inbox, newest first; None when it could not be loaded."""
        try:
            return app_messages(await self.api.notifications())
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            _LOGGER.debug("Notifications failed: %s", err)
            return None

    async def _load_wallet_moves(self, actions: list[LoyaltyAction]) -> list[WalletMove]:
        """Recent movements from the wallet history, where Biedronka keeps one.

        The app only reads it for points and bottle-return wallets; sticker
        wallets answer HTTP 503, so a failing wallet is left alone for a day.
        """
        now = datetime.now(WARSAW)
        moves: list[WalletMove] = []
        for action in actions:
            wallet = action.wallet_id
            failed_at = self._history_failed.get(wallet or "")
            if not wallet or (failed_at and now - failed_at < HISTORY_RETRY):
                continue
            try:
                # Like the app: "now" with its UTC offset; history goes back from it.
                payload = await self.api.wallet_history(
                    wallet, now.isoformat(timespec="seconds")
                )
            except BiedronkaAuthError:
                raise
            except BiedronkaError as err:
                _LOGGER.debug("Wallet history %s failed: %s", wallet, err)
                self._history_failed[wallet] = now
                continue
            self._history_failed.pop(wallet, None)
            moves.extend(wallet_moves(payload, wallet, action)[:MAX_WALLET_MOVES])
        return moves

    async def _balance_moves(self, actions: list[LoyaltyAction]) -> list[WalletMove]:
        """A movement for every campaign balance that changed since the last refresh.

        Works for every wallet, history or not. The last balances are stored,
        so a change made while Home Assistant was down is announced too.
        """
        now = datetime.now(WARSAW).isoformat(timespec="seconds")
        moves: list[WalletMove] = []
        changed = False
        for action in actions:
            wallet, balance = action.wallet_id, action.balance
            if not wallet or balance is None:
                continue
            previous = self._balances.get(wallet)
            if previous == balance:
                continue
            changed = True
            self._balances[wallet] = balance
            if previous is not None:
                moves.append(
                    WalletMove(
                        wallet_id=wallet,
                        action=action.name,
                        when=now,
                        delta=balance - previous,
                        balance=balance,
                        unit=action.unit,
                        ends=action.end_on,
                    )
                )
        if changed and not moves:
            await self._save_rewards()
        return moves

    def _rearm_rewards(self, actions: list[LoyaltyAction]) -> bool:
        """Forget a reward once the balance drops below it, so reaching it again
        (after claiming one) is announced again. True when a stored key was removed."""
        changed = False
        for action in actions:
            if action.balance is None:
                continue
            for reward in action.rewards:
                cost = reward.get("cost")
                if cost is not None and cost > action.balance:
                    if self._notified.pop(RewardReady(action, reward).key, None) is not None:
                        changed = True
        return changed

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
        data.card_offers = list(self._card_offers)

    async def _load_vouchers(self) -> list[Voucher] | None:
        """Coupons from the offers tab. None when the request failed."""
        try:
            payload = await self.api.coupons()
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            _LOGGER.debug("Coupons failed: %s", err)
            return None
        coupons = payload.get("coupons") if isinstance(payload, dict) else None
        if not isinstance(coupons, list):
            return []
        vouchers = [
            Voucher.from_payload(item) for item in coupons if isinstance(item, dict)
        ]
        return sorted(vouchers, key=lambda voucher: _parse_dt(voucher.valid_to) or EPOCH)

    async def _load_shakeomats(self) -> list[ShakeomatOffer] | None:
        """Collect every Shakeomat card the dashboard exposes.

        None when the dashboard or one of its carousels did not respond, so
        the caller can keep the previous cards instead of a partial set.
        """
        try:
            dashboard = await self.api.dashboard()
        except BiedronkaAuthError:
            raise
        except BiedronkaError as err:
            _LOGGER.debug("Dashboard failed: %s", err)
            return None
        if not isinstance(dashboard, (list, dict)):
            _LOGGER.debug("Dashboard payload was not usable")
            return None

        assets = await self._load_assets()
        now = datetime.now(WARSAW)
        offers: dict[str, ShakeomatOffer] = {}
        revealed: list[dict[str, Any]] = []
        personal: dict[str, ShakeomatReward] = {}
        for section in as_sections(dashboard):
            if str(section.get("type") or "").upper() != "CAROUSELS":
                continue
            param = section.get("param")
            if not param:
                continue
            try:
                carousel = await self.api.carousel(str(param))
            except BiedronkaAuthError:
                raise
            except BiedronkaError as err:
                _LOGGER.debug("Carousel %s failed: %s", param, err)
                return None
            items = carousel.get("items") if isinstance(carousel, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("from_shakeomat") is True:
                    revealed.append(item)
                    continue
                if _is_personal_offer(carousel, item):
                    offer = _reward_from_payload("", item, REWARD_SOURCE_PERSONAL)
                    end = _parse_dt(offer.valid_to)
                    if offer.promo_id and not (end and end <= now):
                        offer.offer_id = f"personal_{offer.promo_id}"
                        personal.setdefault(offer.promo_id, offer)
                    continue
                if str(item.get("type") or "").upper() not in SHAKEOMAT_TYPES:
                    continue
                offer = self._classify(item, now, assets)
                key = offer.offer_id or f"anonymous_{len(offers)}"
                offers.setdefault(key, offer)

        if self._remember_revealed(revealed, now):
            self._prune_rewards()
            await self._save_rewards()
        self._card_offers = list(personal.values())

        return sorted(
            offers.values(), key=lambda offer: _parse_dt(offer.available_from) or EPOCH
        )

    def _remember_revealed(self, items: list[dict[str, Any]], now: datetime) -> bool:
        """Keep offers shaken in the phone app, and fill gaps in ones we revealed.

        Once revealed, a Shakeomat card turns into a regular product offer with
        `from_shakeomat: true`, so this is the only place they can be seen.
        """
        changed = False
        for item in items:
            found = _reward_from_payload("", item, REWARD_SOURCE_APP)
            if not found.promo_id:
                continue
            end = _parse_dt(found.valid_to)
            if end and end <= now:
                continue
            existing = next(
                (
                    reward
                    for reward in self._rewards.values()
                    if reward.promo_id == found.promo_id
                    or (
                        found.name is not None
                        and reward.name == found.name
                        and reward.valid_to == found.valid_to
                    )
                ),
                None,
            )
            if existing is None:
                found.offer_id = f"app_{found.promo_id}"
                self._rewards[found.offer_id] = found
                changed = True
                continue
            for name in REWARD_DATA_FIELDS:
                if getattr(existing, name) in (None, []) and getattr(found, name):
                    setattr(existing, name, getattr(found, name))
                    changed = True
        return changed

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
        notified = stored.get("notified") if isinstance(stored, dict) else None
        if isinstance(notified, dict):
            self._notified = {str(key): value for key, value in notified.items()}
        kinds = stored.get("notified_kinds") if isinstance(stored, dict) else None
        if isinstance(kinds, list):
            self._notified_kinds = {str(kind) for kind in kinds}
        balances = stored.get("balances") if isinstance(stored, dict) else None
        if isinstance(balances, dict):
            self._balances = {
                str(wallet): float(value)
                for wallet, value in balances.items()
                if isinstance(value, (int, float))
            }
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
        payload: Any = None
        source = RECEIPT_SOURCE_DETAILS
        if tx.get("is_e_receipt_available"):
            try:
                payload = await self.api.e_receipt(tx_id)
                source = RECEIPT_SOURCE_E_RECEIPT
            except BiedronkaError as err:
                _LOGGER.debug(
                    "E-receipt %s unavailable (%s), falling back to transaction details",
                    tx_id,
                    err,
                )
                payload = None
        if payload is None:
            try:
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
            {
                "rewards": [reward.as_dict() for reward in self._rewards.values()],
                "notified": self._notified,
                "notified_kinds": sorted(self._notified_kinds),
                "balances": self._balances,
            }
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
        if refresh:
            await self.async_request_refresh()
        return reward.as_dict()

