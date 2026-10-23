"""App messages, sticker movements, ready rewards and the product services."""

from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biedronka.const import DOMAIN
from custom_components.biedronka.exceptions import BiedronkaError
from custom_components.biedronka.models import count_with_unit

from .test_offers import J4U, STICKERS, OffersApi, _patched, _refresh, _setup
from .test_store import _store


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"card_number": "1234", "access_token": "a", "refresh_token": "r"},
        options={"auto_shakeomat": False},
        unique_id="1234",
    )
    entry.add_to_hass(hass)
    return entry


def _message(message_id: int, created_at: str, **overrides) -> dict:
    """Shaped like the app's notification model (qc8)."""
    item = {
        "id": message_id,
        "title": "Nowe gazetki",
        "message_preview": "Sprawdź promocje na ten tydzień",
        "message": "<p>Sprawdź promocje na ten tydzień</p>",
        "created_at": created_at,
        "main_image_url": "https://example.com/m.png",
        "category": "OFFERS",
    }
    item.update(overrides)
    return item


def _move(when: str, delta: int, label: str = "Zakupy") -> dict:
    return {
        "transaction_datetime": when,
        "balance_delta": delta,
        "operation_type_label": label,
        "additional_info": None,
    }


async def test_new_message_is_announced_but_not_the_inbox_on_first_run(
    hass, entry, enable_custom_integrations
):
    api = OffersApi({J4U: []})
    api.notifications_payload = {"notifications": [_message(1, "2026-10-01T10:00:00Z")]}
    await _setup(hass, entry, api)

    event = hass.states.get("event.moja_biedronka_1234_new_app_message")
    assert event.state == "unknown"
    messages = hass.states.get("sensor.moja_biedronka_1234_messages")
    assert messages.state == "1"

    api.notifications_payload = {
        "notifications": [
            _message(1, "2026-10-01T10:00:00Z"),
            _message(2, "2026-10-12T08:00:00Z", title="Kupon", message_preview="-20%"),
        ]
    }
    await _refresh(hass, entry)

    event = hass.states.get("event.moja_biedronka_1234_new_app_message")
    assert event.attributes["summary"] == "Kupon: -20%"
    assert event.attributes["image_url"] == "https://example.com/m.png"
    messages = hass.states.get("sensor.moja_biedronka_1234_messages")
    assert messages.state == "2"
    assert messages.attributes["latest"]["title"] == "Kupon"


async def test_failed_first_inbox_load_does_not_flood_later(
    hass, entry, enable_custom_integrations
):
    class FailingInbox(OffersApi):
        fail = True

        async def notifications(self):
            if self.fail:
                raise BiedronkaError("http_500")
            return await super().notifications()

    api = FailingInbox({J4U: []})
    await _setup(hass, entry, api)

    api.fail = False
    api.notifications_payload = {"notifications": [_message(1, "2026-10-01T10:00:00Z")]}
    await _refresh(hass, entry)

    assert hass.states.get("event.moja_biedronka_1234_new_app_message").state == "unknown"
    assert hass.states.get("sensor.moja_biedronka_1234_messages").state == "1"


async def test_stickers_and_ready_reward_events(
    hass, entry, enable_custom_integrations, freezer
):
    api = OffersApi({J4U: []})
    api.loyalty = [STICKERS]
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 8}]
    api.history_payload = {"transactions": [_move("2026-10-10T12:00:00Z", 8)]}
    await _setup(hass, entry, api)

    assert hass.states.get("event.moja_biedronka_1234_stickers_change").state == "unknown"
    assert hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim").state == "unknown"
    balance = hass.states.get("sensor.moja_biedronka_1234_loyalty_balance")
    assert [move["delta"] for move in balance.attributes["history"]] == [8]

    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 10}]
    api.history_payload = {
        "transactions": [
            _move("2026-10-10T12:00:00Z", 8),
            _move("2026-10-12T18:30:00Z", 2, "Naklejki za zakupy"),
        ]
    }
    await _refresh(hass, entry)

    # The event comes from the balance change, the history only fills attributes.
    stickers = hass.states.get("event.moja_biedronka_1234_stickers_change")
    assert stickers.attributes["summary"] == "+2 naklejki (Gang Zaradniaków). Masz teraz 10."
    balance = hass.states.get("sensor.moja_biedronka_1234_loyalty_balance")
    assert balance.attributes["history"][0]["summary"] == (
        "+2 naklejki (Gang Zaradniaków): Naklejki za zakupy. Masz teraz 10."
    )
    reward = hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim")
    assert reward.attributes["summary"] == (
        "Gang Zaradniaków: masz 10 naklejek — możesz odebrać Książka za darmo "
        "(10 naklejek)."
    )
    first_fired = reward.state

    # Event state is the trigger time truncated to milliseconds. Step the clock
    # so a later announcement cannot land on the same state string.
    freezer.tick(timedelta(seconds=1))

    # Claiming the reward takes the balance below it; reaching it again is news.
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 0}]
    await _refresh(hass, entry)
    assert hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim").state == first_fired
    freezer.tick(timedelta(seconds=1))
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 10}]
    await _refresh(hass, entry)
    assert hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim").state != first_fired


async def test_claimed_reward_is_announced_again_after_restart(
    hass, entry, enable_custom_integrations
):
    """Dropping below a reward must be stored, or a restart keeps the old key."""
    api = OffersApi({J4U: []})
    api.loyalty = [STICKERS]
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 8}]
    await _setup(hass, entry, api)

    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 10}]
    await _refresh(hass, entry)
    assert hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim").state != (
        "unknown"
    )

    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 0}]
    await _refresh(hass, entry)

    first, second = _patched(api)
    with first, second:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 10}]
    await _refresh(hass, entry)
    reward = hass.states.get("event.moja_biedronka_1234_reward_ready_to_claim")
    assert reward.state != "unknown"
    assert "Książka za darmo" in reward.attributes["summary"]


async def test_sticker_wallet_without_history(hass, entry, enable_custom_integrations):
    """Sticker wallets answer 503 for history: balance changes still count."""

    class NoHistory(OffersApi):
        history_calls = 0

        async def wallet_history(self, wallet_id, end_date, page=1):
            self.history_calls += 1
            raise BiedronkaError("http_503")

    api = NoHistory({J4U: []})
    api.loyalty = [STICKERS]
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 5}]
    await _setup(hass, entry, api)

    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 7}]
    await _refresh(hass, entry)

    stickers = hass.states.get("event.moja_biedronka_1234_stickers_change")
    assert stickers.attributes["summary"] == "+2 naklejki (Gang Zaradniaków). Masz teraz 7."
    assert stickers.attributes["delta"] == 2
    # The failing history is not asked again on every refresh.
    assert api.history_calls == 1

    # Same balance: nothing new.
    fired = stickers.state
    await _refresh(hass, entry)
    assert hass.states.get("event.moja_biedronka_1234_stickers_change").state == fired


class ProductApi(OffersApi):
    store = None
    calls: list

    async def users_me(self, refresh: bool = False):
        return {"card_number": "1234", "store": self.store}

    async def product_price(self, store_code, ean):
        self.calls.append(("price", store_code, ean))
        return {"name": "Mleko 3,2%", "price": "3.49", "regular_price": "3.99"}

    async def product_stock(self, store_code, ean):
        raise BiedronkaError("http_404")

    async def product_stock_nearby(self, ean, latitude, longitude, user_store):
        self.calls.append(("nearby", ean, latitude, longitude, user_store))
        return [{"store": {"code": "Z9", "city": "Warszawa"}, "coverage": "HIGH"}]

    async def product_search(self, query):
        self.calls.append(("search", query))
        return [{"name": "Mleko 3,2%", "sap_product_number": "123"}]


async def test_product_services(hass, entry, enable_custom_integrations):
    api = ProductApi({J4U: []})
    api.calls = []
    api.store = _store("2026-10-13", "2026-10-14")
    await _setup(hass, entry, api)

    info = await hass.services.async_call(
        DOMAIN, "product_info", {"ean": "5900512300108"}, blocking=True, return_response=True
    )
    assert info["store"]["code"] == "Z123"
    assert info["price"]["price"] == "3.49"
    assert info["stock"] is None

    nearby = await hass.services.async_call(
        DOMAIN,
        "product_stock_nearby",
        {"ean": "5900512300108", "config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert nearby["stores"][0]["coverage"] == "HIGH"
    assert api.calls[-1] == (
        "nearby",
        "5900512300108",
        hass.config.latitude,
        hass.config.longitude,
        "Z123",
    )

    found = await hass.services.async_call(
        DOMAIN, "product_search", {"query": "mleko"}, blocking=True, return_response=True
    )
    assert found == {"products": [{"name": "Mleko 3,2%", "sap_product_number": "123"}]}


async def test_product_info_needs_a_store(hass, entry, enable_custom_integrations):
    api = ProductApi({J4U: []})
    api.calls = []
    await _setup(hass, entry, api)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "product_info",
            {"ean": "5900512300108"},
            blocking=True,
            return_response=True,
        )


@pytest.mark.parametrize(
    ("count", "unit", "text"),
    [
        (1, "naklejki", "1 naklejka"),
        (3, "naklejki", "3 naklejki"),
        (12, "naklejki", "12 naklejek"),
        (22, "naklejki", "22 naklejki"),
        (5, "punkty", "5 punktów"),
        (2, None, "2 naklejki"),
    ],
)
def test_count_with_unit(count, unit, text):
    assert count_with_unit(count, unit) == text
