"""Last five receipts: download, in-session cache and prune."""

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biedronka.const import DEFAULT_AUTO_SHAKEOMAT, DOMAIN, MAX_RECEIPTS
from custom_components.biedronka.coordinator import WARSAW, BiedronkaCoordinator
from custom_components.biedronka.exceptions import BiedronkaError
from custom_components.biedronka.models import (
    RECEIPT_SOURCE_DETAILS,
    RECEIPT_SOURCE_E_RECEIPT,
    slim_receipt_lines,
)


def _iso(offset_hours: float) -> str:
    return (datetime.now(WARSAW) + timedelta(hours=offset_hours)).isoformat()


def _tx(tx_id: str, *, e_receipt: bool = True, hours: float = 0, **kwargs) -> dict:
    return {
        "id": tx_id,
        "date": kwargs.get("date", _iso(hours)),
        "total_price": kwargs.get("total_price", 12.5),
        "store_name": kwargs.get("store_name", f"Sklep {tx_id}"),
        "receipt_num": kwargs.get("receipt_num", f"R{tx_id}"),
        "is_e_receipt_available": e_receipt,
    }


class ReceiptFakeApi:
    """Stand-in covering transaction list, details and fiscal JSON."""

    def __init__(self, transactions: list[dict]):
        self.transactions_page = {
            "transactions": transactions,
            "page_count": 1,
            "page_number": 1,
        }
        self.e_receipt_calls: list[str] = []
        self.details_calls: list[str] = []
        self.fail_e_receipt: set[str] = set()
        self.fail_details: set[str] = set()
        self.e_receipt_payload: dict | None = None

    async def transactions(self, page: int = 1):
        return self.transactions_page

    async def e_receipt(self, transaction_id: str, output_format: str = "json"):
        self.e_receipt_calls.append(transaction_id)
        if transaction_id in self.fail_e_receipt:
            raise BiedronkaError("http_404")
        if self.e_receipt_payload is not None:
            return self.e_receipt_payload
        return {"fiscal": True, "id": transaction_id, "format": output_format}

    async def transaction_details(self, transaction_id: str):
        self.details_calls.append(transaction_id)
        if transaction_id in self.fail_details:
            raise BiedronkaError("http_404")
        return {
            "id": transaction_id,
            "items": [{"name": "Chleb", "ean": "123"}],
            "store_name": f"Sklep {transaction_id}",
            "receipt_num": f"R{transaction_id}",
            "total_price": 12.5,
        }


@pytest.fixture
def coordinator_factory(hass):
    def _make(api: ReceiptFakeApi) -> BiedronkaCoordinator:
        entry = MockConfigEntry(domain=DOMAIN, data={"card_number": "1234"}, options={})
        entry.add_to_hass(hass)
        return BiedronkaCoordinator(hass, entry, api)

    return _make


def test_auto_shakeomat_is_off_by_default(coordinator_factory):
    assert DEFAULT_AUTO_SHAKEOMAT is False
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    assert coordinator.auto_shakeomat is False


async def test_missing_receipts_are_fetched_once(coordinator_factory):
    api = ReceiptFakeApi([_tx("a"), _tx("b"), _tx("c")])
    coordinator = coordinator_factory(api)

    first = await coordinator._sync_receipts(api.transactions_page["transactions"])
    second = await coordinator._sync_receipts(api.transactions_page["transactions"])

    assert [receipt.id for receipt in first] == ["a", "b", "c"]
    assert [receipt.source for receipt in first] == [RECEIPT_SOURCE_E_RECEIPT] * 3
    assert api.e_receipt_calls == ["a", "b", "c"]
    assert [receipt.id for receipt in second] == ["a", "b", "c"]
    assert api.e_receipt_calls == ["a", "b", "c"]
    assert api.details_calls == []


async def test_only_the_newest_five_are_kept(coordinator_factory):
    newest = [_tx(str(i), hours=-i) for i in range(7)]
    api = ReceiptFakeApi(newest)
    coordinator = coordinator_factory(api)

    receipts = await coordinator._sync_receipts(newest)

    assert [receipt.id for receipt in receipts] == [str(i) for i in range(MAX_RECEIPTS)]
    assert api.e_receipt_calls == [str(i) for i in range(MAX_RECEIPTS)]
    assert "5" not in coordinator._receipts
    assert "6" not in coordinator._receipts


async def test_older_cached_receipts_are_pruned(coordinator_factory):
    first_page = [_tx(str(i), hours=-i) for i in range(5)]
    api = ReceiptFakeApi(first_page)
    coordinator = coordinator_factory(api)
    await coordinator._sync_receipts(first_page)

    newer = [_tx("new", hours=1)] + first_page[:4]
    api.transactions_page["transactions"] = newer
    receipts = await coordinator._sync_receipts(newer)

    assert [receipt.id for receipt in receipts] == ["new", "0", "1", "2", "3"]
    assert "4" not in coordinator._receipts
    assert api.e_receipt_calls[-1] == "new"


async def test_restart_fetches_receipts_again(coordinator_factory, hass):
    api = ReceiptFakeApi([_tx("kept")])
    coordinator = coordinator_factory(api)
    await coordinator._sync_receipts(api.transactions_page["transactions"])

    revived = BiedronkaCoordinator(hass, coordinator.entry, api)
    receipts = await revived._sync_receipts(api.transactions_page["transactions"])

    assert [receipt.id for receipt in receipts] == ["kept"]
    assert api.e_receipt_calls == ["kept", "kept"]


async def test_details_are_used_when_e_receipt_is_unavailable(coordinator_factory):
    api = ReceiptFakeApi([_tx("paper", e_receipt=False)])
    coordinator = coordinator_factory(api)

    receipts = await coordinator._sync_receipts(api.transactions_page["transactions"])

    assert receipts[0].source == RECEIPT_SOURCE_DETAILS
    assert receipts[0].as_dict()["lines"] == [{"sellLine": {"name": "Chleb"}}]
    assert "ean" not in receipts[0].as_dict()["lines"][0]["sellLine"]
    assert "payload" not in receipts[0].as_dict()
    assert api.e_receipt_calls == []
    assert api.details_calls == ["paper"]


async def test_one_failed_receipt_does_not_drop_the_rest(coordinator_factory):
    api = ReceiptFakeApi([_tx("ok"), _tx("bad"), _tx("also")])
    api.fail_e_receipt = {"bad"}
    coordinator = coordinator_factory(api)

    receipts = await coordinator._sync_receipts(api.transactions_page["transactions"])

    assert [receipt.id for receipt in receipts] == ["ok", "also"]
    assert api.e_receipt_calls == ["ok", "bad", "also"]


FAT_E_RECEIPT = {
    "nip": "1138492354",
    "header": {"cashier": "Jan", "printoutNumber": "9020"},
    "lines": [
        {
            "headerLine": {
                "date": "2026-08-29",
                "time": "11:24",
                "cashier": "Jan",
            }
        },
        {
            "sellLine": {
                "name": "PapierQueen8rolek",
                "vatId": "A",
                "price": 849,
                "total": 5094,
                "quantity": "6",
                "isStorno": False,
                "unit": "szt",
                "ptu": "A",
                "ean": "5900000000000",
            }
        },
        {
            "discountLine": {
                "base": 5094,
                "value": 1698,
                "isDiscount": True,
                "isPercent": False,
                "isStorno": False,
                "vatId": "A",
                "name": "Promocja",
            }
        },
        {"paymentLine": {"type": "card", "value": 3396}},
        {"discountSummary": {"discounts": 1698, "extra": True}},
        {
            "vatSummary": {
                "currency": "PLN",
                "totalVat": 635,
                "vatRatesSummary": [
                    {
                        "vatId": "A",
                        "vatRate": 2300,
                        "vatSale": 3396,
                        "vatAmount": 635,
                        "vatNet": 2761,
                    }
                ],
            }
        },
        {
            "sumInCurrency": {
                "fiscalTotal": 3396,
                "totalWithPacks": 3396,
                "currency": "PLN",
                "change": 0,
            }
        },
    ],
    "qr": "https://example.test/qr",
}


def test_e_receipt_lines_drop_unused_fields():
    assert slim_receipt_lines(FAT_E_RECEIPT) == [
        {
            "sellLine": {
                "name": "PapierQueen8rolek",
                "vatId": "A",
                "price": 849,
                "total": 5094,
                "quantity": "6",
                "isStorno": False,
            }
        },
        {
            "discountLine": {
                "base": 5094,
                "value": 1698,
                "isDiscount": True,
                "isPercent": False,
                "isStorno": False,
                "vatId": "A",
            }
        },
        {"discountSummary": {"discounts": 1698}},
        {
            "vatSummary": {
                "currency": "PLN",
                "vatRatesSummary": [
                    {
                        "vatId": "A",
                        "vatRate": 2300,
                        "vatSale": 3396,
                        "vatAmount": 635,
                    }
                ],
            }
        },
        {
            "sumInCurrency": {
                "fiscalTotal": 3396,
                "totalWithPacks": 3396,
                "currency": "PLN",
            }
        },
    ]


async def test_e_receipt_is_stored_without_payload(coordinator_factory):
    api = ReceiptFakeApi([_tx("2608299020137101")])
    api.e_receipt_payload = FAT_E_RECEIPT
    coordinator = coordinator_factory(api)

    receipts = await coordinator._sync_receipts(api.transactions_page["transactions"])
    exported = receipts[0].as_dict()

    assert exported["id"] == "2608299020137101"
    assert exported["source"] == RECEIPT_SOURCE_E_RECEIPT
    assert "payload" not in exported
    assert "nip" not in exported
    assert "header" not in exported
    assert exported["lines"][0]["sellLine"] == {
        "name": "PapierQueen8rolek",
        "vatId": "A",
        "price": 849,
        "total": 5094,
        "quantity": "6",
        "isStorno": False,
    }
    assert receipts[0].payload is None
