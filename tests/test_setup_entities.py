"""Entity set-up and removal of the pre-rework Shakeomat slot entities."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biedronka.const import DOMAIN

from .test_shakeomat import FakeApi, _item


class FullFakeApi(FakeApi):
    """Adds the non-Shakeomat endpoints touched during a coordinator refresh."""

    async def users_me(self, refresh: bool = False):
        return {"card_number": "1234", "first_name": "Ala"}

    async def transactions(self, page: int = 1):
        return {"transactions": [], "page_count": 1}

    async def transaction_details(self, transaction_id: str):
        return {}

    async def e_receipt(self, transaction_id: str, output_format: str = "json"):
        return {}


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "card_number": "1234",
            "access_token": "access",
            "refresh_token": "refresh",
        },
        options={"auto_shakeomat": False},
        unique_id="1234",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, api):
    with (
        patch("custom_components.biedronka.BiedronkaApi", return_value=api),
        patch("custom_components.biedronka.async_get_clientsession", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_entities_are_created(hass, entry, enable_custom_integrations):
    api = FullFakeApi({"c1": [_item("a"), _item("b")]})
    await _setup(hass, entry, api)

    assert hass.states.get("sensor.moja_biedronka_shakeomat").state == "available"
    assert hass.states.get("sensor.moja_biedronka_shakeomats_ready").state == "2"
    assert hass.states.get("sensor.moja_biedronka_recent_receipts").state == "0"
    assert hass.states.get("button.moja_biedronka_reveal_shakeomats") is not None
    assert hass.states.get("sensor.moja_biedronka_shakeomat_1") is None


async def test_legacy_slot_entities_are_removed(
    hass, entry, enable_custom_integrations
):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor", DOMAIN, "1234_shakeomat_2", config_entry=entry
    )

    api = FullFakeApi({"c1": [_item("a")]})
    await _setup(hass, entry, api)

    assert registry.async_get(legacy.entity_id) is None
