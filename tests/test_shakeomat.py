"""Shakeomat discovery, activation and reward history."""

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biedronka.const import DOMAIN
from custom_components.biedronka.coordinator import WARSAW, BiedronkaCoordinator
from custom_components.biedronka.exceptions import BiedronkaError
from custom_components.biedronka.models import (
    STATUS_AVAILABLE,
    STATUS_CLAIMED,
    STATUS_COOLDOWN,
    BiedronkaData,
)


def _iso(offset_hours: float) -> str:
    return (datetime.now(WARSAW) + timedelta(hours=offset_hours)).isoformat()


def _item(offer_id: str, item_type: str = "SHAKEOMAT", **kwargs) -> dict:
    return {
        "type": item_type,
        "offer_id": offer_id,
        "available_from": kwargs.get("available_from", _iso(-1)),
        "available_to": kwargs.get("available_to", _iso(6)),
        "meta": kwargs.get("meta", "assets-1"),
        "offer_type": item_type,
    }


class FakeApi:
    """Minimal stand-in for BiedronkaApi covering the Shakeomat endpoints."""

    def __init__(
        self, carousels: dict[str, list[dict]], assets: list[dict] | None = None
    ):
        self.carousels = carousels
        self.assets = assets or []
        self.activated: list[str] = []
        self.fail_on: set[str] = set()

    async def dashboard(self):
        return {
            "sections": [
                {"type": "CAROUSELS", "param": param, "slug": param}
                for param in self.carousels
            ]
        }

    async def carousel(self, carousel_id: str):
        return {"id": carousel_id, "items": self.carousels[carousel_id]}

    async def shakeomat_assets(self):
        return self.assets

    async def reveal_and_activate(self, offer_id: str):
        self.activated.append(offer_id)
        if offer_id in self.fail_on:
            raise BiedronkaError("http_409")
        return {
            "name": f"Oferta {offer_id}",
            "details": "Limit 1 sztuka",
            "price": "4,99",
            "discount": "-50%",
            "image_url": f"https://example.invalid/{offer_id}.png",
            "start": _iso(0),
            "end": _iso(24),
        }


@pytest.fixture
def coordinator_factory(hass):
    def _make(api: FakeApi) -> BiedronkaCoordinator:
        entry = MockConfigEntry(domain=DOMAIN, data={"card_number": "1234"}, options={})
        entry.add_to_hass(hass)
        return BiedronkaCoordinator(hass, entry, api)

    return _make


async def test_two_offers_of_the_same_type_are_both_kept(coordinator_factory):
    api = FakeApi(
        {
            "c1": [
                _item("second", available_from=_iso(3), available_to=_iso(9)),
                _item("first"),
            ]
        }
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    offers = await coordinator._load_shakeomats()

    assert [offer.offer_id for offer in offers] == ["first", "second"]
    assert [offer.status for offer in offers] == [STATUS_AVAILABLE, STATUS_COOLDOWN]


async def test_offers_from_several_carousels_are_deduplicated(coordinator_factory):
    api = FakeApi(
        {
            "c1": [
                _item("a"),
                _item("b", "SHAKEOMARKA"),
                {"type": "ACTION", "id": "x"},
            ],
            "c2": [_item("a"), _item("c", "SHAKEOMAT_2")],
        }
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    offers = await coordinator._load_shakeomats()

    assert sorted(offer.offer_id for offer in offers) == ["a", "b", "c"]


async def test_assets_name_is_matched_by_meta(coordinator_factory):
    api = FakeApi(
        {"c1": [_item("a", meta="winter")]},
        assets=[{"id": "winter", "name": "Zimowy Shakeomat"}],
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    offers = await coordinator._load_shakeomats()

    assert offers[0].assets_name == "Zimowy Shakeomat"


async def test_claimed_offer_survives_the_next_one_appearing(coordinator_factory):
    api = FakeApi({"c1": [_item("first")]})
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    await coordinator.async_activate_shakeomat("first", refresh=False)

    api.carousels["c1"] = [_item("second")]
    offers = await coordinator._load_shakeomats()
    data = BiedronkaData()
    coordinator._apply_shakeomats(data, offers)

    assert [offer.offer_id for offer in offers] == ["second"]
    assert data.last_reward is not None
    assert data.last_reward.offer_id == "first"
    assert [reward.offer_id for reward in data.reward_history] == ["first"]


async def test_already_claimed_offer_is_not_activated_twice(coordinator_factory):
    api = FakeApi({"c1": [_item("first")]})
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    await coordinator.async_activate_shakeomat("first", refresh=False)
    offers = await coordinator._load_shakeomats()

    assert offers[0].status == STATUS_CLAIMED

    await coordinator._activate_offers(offers, refresh=False)

    assert api.activated == ["first"]


async def test_activate_all_reveals_every_offer_and_survives_one_failure(
    coordinator_factory,
):
    api = FakeApi({"c1": [_item("a"), _item("b"), _item("c")]})
    api.fail_on = {"b"}
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    claimed = await coordinator.async_activate_all(refresh=False)

    assert api.activated == ["a", "b", "c"]
    assert [reward["offer_id"] for reward in claimed] == ["a", "c"]


async def test_rewards_are_restored_from_storage(coordinator_factory, hass):
    api = FakeApi({"c1": [_item("first")]})
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()
    await coordinator.async_activate_shakeomat("first", refresh=False)

    revived = BiedronkaCoordinator(hass, coordinator.entry, api)
    await revived.async_load_rewards()

    offers = await revived._load_shakeomats()

    assert offers[0].status == STATUS_CLAIMED
