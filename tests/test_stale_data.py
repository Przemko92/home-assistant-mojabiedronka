"""A bad API response keeps the last correct entity state for 24 hours."""

from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biedronka.const import DOMAIN
from custom_components.biedronka.exceptions import BiedronkaError

from .test_messages_rewards import _message
from .test_offers import J4U, STICKERS, OffersApi, _refresh, _setup
from .test_shakeomat import _iso, _item
from .test_store import _store


@pytest.fixture
def entry(hass):
    config = MockConfigEntry(
        domain=DOMAIN,
        data={"card_number": "1234", "access_token": "a", "refresh_token": "r"},
        options={"auto_shakeomat": False},
        unique_id="1234",
    )
    config.add_to_hass(hass)
    return config


def _entity(hass, domain: str, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return hass.states.get(entity_id)


class FlakyApi(OffersApi):
    """Same endpoints as a normal refresh, with chosen calls that fail."""

    def __init__(self, carousels: dict):
        super().__init__(carousels)
        self.fail: set[str] = set()
        self.store = None

    async def users_me(self, refresh: bool = False):
        if "user" in self.fail:
            raise BiedronkaError("http_500")
        return {"card_number": "1234", "first_name": "Ala", "store": self.store}

    async def transactions(self, page: int = 1):
        if "transactions" in self.fail:
            raise BiedronkaError("http_500")
        return {
            "transactions": [
                {
                    "id": "t1",
                    "total_price": 12.5,
                    "date": dt_util.now().isoformat(),
                    "store_name": "Biedronka",
                }
            ],
            "page_count": 1,
        }

    async def dashboard(self):
        if "dashboard" in self.fail:
            raise BiedronkaError("http_500")
        return await super().dashboard()

    async def carousel(self, carousel_id: str):
        if "carousel" in self.fail:
            raise BiedronkaError("http_500")
        return await super().carousel(carousel_id)

    async def coupons(self):
        if "coupons" in self.fail:
            raise BiedronkaError("http_500")
        return await super().coupons()

    async def loyalty_actions(self):
        if "loyalty" in self.fail:
            raise BiedronkaError("http_500")
        return await super().loyalty_actions()

    async def notifications(self):
        if "messages" in self.fail:
            raise BiedronkaError("http_500")
        return await super().notifications()


def _ready(api: FlakyApi) -> None:
    today = dt_util.now().date()
    api.store = _store(today.isoformat(), (today + timedelta(days=1)).isoformat())
    api.coupons_payload = {
        "coupons": [
            {
                "number": "V1",
                "status": "READY_TO_USE",
                "meta": "E-voucher",
                "discount_description": {"pl": "-10 zł"},
                "valid_from": _iso(-1),
                "valid_to": _iso(72),
            }
        ]
    }
    api.loyalty = [STICKERS]
    api.wallets_payload = [{"perspectiv_id": "Wallet100", "balance": 12}]
    api.notifications_payload = {"notifications": [_message(1, "2026-10-01T10:00:00Z")]}


async def test_failed_responses_keep_the_last_correct_state(
    hass, entry, enable_custom_integrations, freezer
):
    api = FlakyApi({J4U: [_item("first")]})
    _ready(api)
    await _setup(hass, entry, api)

    shakeomat = hass.states.get("sensor.moja_biedronka_1234_shakeomat")
    assert shakeomat.state == "available"
    assert shakeomat.attributes["offer_id"] == "first"
    assert hass.states.get("sensor.moja_biedronka_1234_shakeomats_ready").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_vouchers").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_messages").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_loyalty_balance").state == "12.0"
    assert hass.states.get("sensor.moja_biedronka_1234_store_opening_hours").state == (
        "00:00–23:59"
    )
    assert _entity(hass, "sensor", "1234_last_transaction").state == "12.5"

    api.fail.update({"user", "transactions", "dashboard", "coupons", "loyalty", "messages"})
    await _refresh(hass, entry)

    shakeomat = hass.states.get("sensor.moja_biedronka_1234_shakeomat")
    assert shakeomat.state == "available"
    assert shakeomat.attributes["offer_id"] == "first"
    assert hass.states.get("sensor.moja_biedronka_1234_shakeomats_ready").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_vouchers").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_messages").state == "1"
    assert hass.states.get("sensor.moja_biedronka_1234_loyalty_balance").state == "12.0"
    assert hass.states.get("sensor.moja_biedronka_1234_store_opening_hours").state == (
        "00:00–23:59"
    )
    assert _entity(hass, "sensor", "1234_last_transaction").state == "12.5"
    assert hass.states.get("binary_sensor.moja_biedronka_1234_store_open").state == "on"

    freezer.tick(timedelta(hours=24, seconds=1))
    await _refresh(hass, entry)

    assert hass.states.get("sensor.moja_biedronka_1234_shakeomat").state == STATE_UNKNOWN
    assert hass.states.get("sensor.moja_biedronka_1234_shakeomats_ready").state == (
        STATE_UNKNOWN
    )
    assert hass.states.get("sensor.moja_biedronka_1234_vouchers").state == STATE_UNKNOWN
    assert hass.states.get("sensor.moja_biedronka_1234_messages").state == STATE_UNKNOWN
    assert hass.states.get("sensor.moja_biedronka_1234_loyalty_balance").state == (
        STATE_UNKNOWN
    )
    assert hass.states.get("sensor.moja_biedronka_1234_store_opening_hours").state == (
        STATE_UNKNOWN
    )
    assert _entity(hass, "sensor", "1234_last_transaction").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.moja_biedronka_1234_store_open").state == (
        STATE_UNKNOWN
    )

    api.fail.clear()
    api.carousels[J4U] = [_item("second")]
    await _refresh(hass, entry)

    shakeomat = hass.states.get("sensor.moja_biedronka_1234_shakeomat")
    assert shakeomat.state == "available"
    assert shakeomat.attributes["offer_id"] == "second"
    assert hass.states.get("sensor.moja_biedronka_1234_vouchers").state == "1"
    assert _entity(hass, "sensor", "1234_last_transaction").state == "12.5"


async def test_a_failed_section_does_not_block_the_ones_that_answered(
    hass, entry, enable_custom_integrations
):
    api = FlakyApi({J4U: [_item("first")]})
    _ready(api)
    api.coupons_payload = {"coupons": []}
    await _setup(hass, entry, api)

    api.fail.add("dashboard")
    api.coupons_payload = {
        "coupons": [
            {
                "number": "V1",
                "status": "READY_TO_USE",
                "meta": "E-voucher",
                "discount_description": {"pl": "-10 zł"},
                "valid_from": _iso(-1),
                "valid_to": _iso(72),
            }
        ]
    }
    await _refresh(hass, entry)

    assert hass.states.get("sensor.moja_biedronka_1234_shakeomat").state == "available"
    assert hass.states.get("sensor.moja_biedronka_1234_vouchers").state == "1"


async def test_a_correct_empty_dashboard_clears_shakeomat(
    hass, entry, enable_custom_integrations
):
    api = FlakyApi({J4U: [_item("first")]})
    await _setup(hass, entry, api)
    assert hass.states.get("sensor.moja_biedronka_1234_shakeomat").state == "available"

    api.carousels[J4U] = []
    await _refresh(hass, entry)

    assert hass.states.get("sensor.moja_biedronka_1234_shakeomat").state == "none"
    assert hass.states.get("sensor.moja_biedronka_1234_shakeomats_ready").state == "0"


async def test_one_failed_carousel_keeps_the_previous_cards(
    hass, entry, enable_custom_integrations
):
    api = FlakyApi({J4U: [_item("first")]})
    await _setup(hass, entry, api)

    api.fail.add("carousel")
    api.carousels[J4U] = []
    await _refresh(hass, entry)

    shakeomat = hass.states.get("sensor.moja_biedronka_1234_shakeomat")
    assert shakeomat.state == "available"
    assert shakeomat.attributes["offer_id"] == "first"
