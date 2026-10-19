"""Tests for safe, deterministic merchant-to-PayPal reconciliation."""

from decimal import Decimal

import pytest

from backend.reconciliation import (
    ReconciliationInputError,
    compare_reconciliation_record,
    parse_reconciliation_csv,
)

VALID_CSV = """merchant_reference,paypal_order_id,expected_amount,currency_code
INV-1001,ORDER-1001,25.00,USD
INV-1002,ORDER-1002,40.50,USD
"""


def test_parses_valid_csv_and_preserves_exact_amounts():
    records = parse_reconciliation_csv(VALID_CSV)

    assert len(records) == 2
    assert records[0]["merchant_reference"] == "INV-1001"
    assert records[0]["paypal_order_id"] == "ORDER-1001"
    assert records[0]["expected_amount"] == Decimal("25.00")
    assert records[0]["currency_code"] == "USD"


@pytest.mark.parametrize(
    "csv_text",
    [
        "",
        "merchant_reference,paypal_order_id,expected_amount\nINV-1,ORDER-1,10.00",
        "merchant_reference,paypal_order_id,expected_amount,currency_code\n",
        VALID_CSV + "INV-1003,ORDER-1003,not-a-number,USD\n",
        VALID_CSV + "INV-1003,ORDER-1003,-1.00,USD\n",
        VALID_CSV + "INV-1003,ORDER-1003,10.123,USD\n",
        VALID_CSV + "INV-1003,ORDER-1003,10.00,US\n",
    ],
)
def test_rejects_invalid_csv_input(csv_text):
    with pytest.raises(ReconciliationInputError):
        parse_reconciliation_csv(csv_text)


def test_rejects_duplicate_or_missing_column_names():
    duplicate_header = (
        "merchant_reference,paypal_order_id,expected_amount,currency_code,"
        "currency_code\n"
        "INV-1,ORDER-1,10.00,USD,USD\n"
    )
    missing_header = (
        "merchant_reference,paypal_order_id,expected_amount\n"
        "INV-1,ORDER-1,10.00\n"
    )

    for csv_text in (duplicate_header, missing_header):
        with pytest.raises(ReconciliationInputError):
            parse_reconciliation_csv(csv_text)


def test_matching_amount_and_currency_returns_matched():
    record = {
        "merchant_reference": "INV-1",
        "paypal_order_id": "ORDER-1",
        "expected_amount": Decimal("25.00"),
        "currency_code": "USD",
    }
    order = {
        "id": "ORDER-1",
        "status": "COMPLETED",
        "purchase_units": [
            {"amount": {"value": "25.00", "currency_code": "USD"}}
        ],
    }

    result = compare_reconciliation_record(record, order)

    assert result["status"] == "matched"
    assert result["settlement_verified"] is False


def test_amount_difference_returns_amount_mismatch():
    record = {
        "merchant_reference": "INV-1",
        "paypal_order_id": "ORDER-1",
        "expected_amount": Decimal("25.00"),
        "currency_code": "USD",
    }
    order = {
        "id": "ORDER-1",
        "purchase_units": [
            {"amount": {"value": "24.99", "currency_code": "USD"}}
        ],
    }

    result = compare_reconciliation_record(record, order)

    assert result["status"] == "amount_mismatch"
    assert result["settlement_verified"] is False


def test_currency_difference_returns_currency_mismatch():
    record = {
        "merchant_reference": "INV-1",
        "paypal_order_id": "ORDER-1",
        "expected_amount": Decimal("25.00"),
        "currency_code": "USD",
    }
    order = {
        "id": "ORDER-1",
        "purchase_units": [
            {"amount": {"value": "25.00", "currency_code": "KES"}}
        ],
    }

    result = compare_reconciliation_record(record, order)

    assert result["status"] == "currency_mismatch"
    assert result["settlement_verified"] is False


@pytest.mark.parametrize(
    "order",
    [
        {},
        {"purchase_units": []},
        {"purchase_units": [{"amount": {"value": "25.00"}}]},
        {
            "purchase_units": [
                {"amount": {"value": "25.00", "currency_code": "USD"}},
                {"amount": {"value": "25.00", "currency_code": "USD"}},
            ]
        },
        {
            "purchase_units": [
                {"amount": {"value": "invalid", "currency_code": "USD"}}
            ]
        },
    ],
)
def test_ambiguous_or_malformed_order_returns_needs_review(order):
    record = {
        "merchant_reference": "INV-1",
        "paypal_order_id": "ORDER-1",
        "expected_amount": Decimal("25.00"),
        "currency_code": "USD",
    }

    result = compare_reconciliation_record(record, order)

    assert result["status"] == "needs_review"
    assert result["settlement_verified"] is False


def test_malformed_csv_quoting_is_rejected():
    malformed_csv = (
        "merchant_reference,paypal_order_id,expected_amount,currency_code\n"
        '"INV-1,ORDER-1,10.00,USD\n'
    )

    with pytest.raises(ReconciliationInputError):
        parse_reconciliation_csv(malformed_csv)


def test_duplicate_detection_flags_every_repeated_identifier():
    from backend.reconciliation import identify_duplicate_records

    records = [
        {
            "merchant_reference": "INV-1",
            "paypal_order_id": "ORDER-1",
        },
        {
            "merchant_reference": "INV-1",
            "paypal_order_id": "ORDER-2",
        },
        {
            "merchant_reference": "INV-3",
            "paypal_order_id": "ORDER-1",
        },
        {
            "merchant_reference": "INV-4",
            "paypal_order_id": "ORDER-4",
        },
    ]

    duplicates = identify_duplicate_records(records)

    assert set(duplicates) == {0, 1, 2}
    assert "merchant reference" in duplicates[0].lower()
    assert "merchant reference" in duplicates[1].lower()
    assert "paypal order id" in duplicates[0].lower()
    assert "paypal order id" in duplicates[2].lower()
    assert 3 not in duplicates


def test_duplicate_detection_does_not_flag_unique_records():
    from backend.reconciliation import identify_duplicate_records

    records = [
        {"merchant_reference": "INV-1", "paypal_order_id": "ORDER-1"},
        {"merchant_reference": "INV-2", "paypal_order_id": "ORDER-2"},
    ]

    assert identify_duplicate_records(records) == {}
