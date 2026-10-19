"""Tests for independent, read-only reconciliation service behavior."""

from decimal import Decimal

import pytest

from backend.paypal_client import PayPalAPIError
from backend.reconciliation import parse_reconciliation_csv
from backend.reconciliation_service import reconcile_csv, reconcile_records


class FakePayPal:
    """Fake PayPal client that records lookups and supports per-ID outcomes."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.requested_ids = []

    def get_order(self, order_id):
        self.requested_ids.append(order_id)
        outcome = self.outcomes.get(order_id)

        if isinstance(outcome, Exception):
            raise outcome

        if outcome is not None:
            return outcome

        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "amount": {
                        "value": "10.00",
                        "currency_code": "USD",
                    }
                }
            ],
        }


def make_records():
    return parse_reconciliation_csv(
        "merchant_reference,paypal_order_id,expected_amount,currency_code\n"
        "INV-1,ORDER-1,10.00,USD\n"
        "INV-2,ORDER-2,10.00,USD\n"
        "INV-3,ORDER-3,10.00,USD\n"
    )


def test_reconcile_csv_matches_valid_order_and_never_verifies_settlement():
    paypal = FakePayPal()

    response = reconcile_csv(
        "merchant_reference,paypal_order_id,expected_amount,currency_code\n"
        "INV-1,ORDER-1,10.00,USD\n",
        paypal,
    )

    assert response["count"] == 1
    assert response["counts"] == {"matched": 1}
    assert response["results"][0]["status"] == "matched"
    assert response["settlement_verified"] is False
    assert response["results"][0]["settlement_verified"] is False
    assert paypal.requested_ids == ["ORDER-1"]


def test_duplicate_rows_are_flagged_and_never_looked_up():
    records = [
        {
            "merchant_reference": "INV-1",
            "paypal_order_id": "ORDER-1",
            "expected_amount": Decimal("10.00"),
            "currency_code": "USD",
        },
        {
            "merchant_reference": "INV-1",
            "paypal_order_id": "ORDER-2",
            "expected_amount": Decimal("10.00"),
            "currency_code": "USD",
        },
        {
            "merchant_reference": "INV-3",
            "paypal_order_id": "ORDER-3",
            "expected_amount": Decimal("10.00"),
            "currency_code": "USD",
        },
    ]
    paypal = FakePayPal()

    response = reconcile_records(records, paypal)

    assert [row["status"] for row in response["results"]] == [
        "needs_review",
        "needs_review",
        "matched",
    ]
    assert paypal.requested_ids == ["ORDER-3"]


def test_only_paypal_404_becomes_order_not_found():
    paypal = FakePayPal(
        {
            "ORDER-1": PayPalAPIError(404, "not found"),
            "ORDER-2": PayPalAPIError(503, "unavailable"),
            "ORDER-3": {
                "id": "ORDER-3",
                "purchase_units": [
                    {"amount": {"value": "10.00", "currency_code": "USD"}}
                ],
            },
        }
    )

    response = reconcile_records(make_records(), paypal)

    assert [row["status"] for row in response["results"]] == [
        "order_not_found",
        "lookup_error",
        "matched",
    ]
    assert response["counts"] == {
        "order_not_found": 1,
        "lookup_error": 1,
        "matched": 1,
    }


@pytest.mark.parametrize(
    ("error", "expected_summary"),
    [
        (RuntimeError("PayPal API request timed out."), "timed out"),
        (RuntimeError("Could not connect to the PayPal API."), "temporarily unavailable"),
    ],
)
def test_runtime_lookup_failures_are_isolated(error, expected_summary):
    paypal = FakePayPal({"ORDER-1": error})

    response = reconcile_records(make_records(), paypal)

    assert response["results"][0]["status"] == "lookup_error"
    assert expected_summary in response["results"][0]["summary"]
    assert response["results"][1]["status"] == "matched"
    assert response["results"][2]["status"] == "matched"


def test_mismatched_returned_order_id_requires_review():
    paypal = FakePayPal(
        {
            "ORDER-1": {
                "id": "SOME-OTHER-ORDER",
                "purchase_units": [
                    {"amount": {"value": "10.00", "currency_code": "USD"}}
                ],
            }
        }
    )

    response = reconcile_records(make_records()[:1], paypal)

    assert response["results"][0]["status"] == "needs_review"
    assert response["results"][0]["settlement_verified"] is False
