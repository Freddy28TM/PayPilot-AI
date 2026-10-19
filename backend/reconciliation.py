"""CSV parsing and deterministic merchant-to-PayPal reconciliation."""

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any

REQUIRED_COLUMNS = (
    "merchant_reference",
    "paypal_order_id",
    "expected_amount",
    "currency_code",
)

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
AMOUNT_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
MAX_CSV_BYTES = 1_000_000
MAX_RECORDS = 1_000


class ReconciliationInputError(ValueError):
    """Raised when a reconciliation CSV is invalid."""


def parse_reconciliation_csv(csv_text: str) -> list[dict[str, Any]]:
    """Parse and validate merchant reconciliation records."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ReconciliationInputError("The CSV file is empty.")

    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ReconciliationInputError("The CSV file is too large.")

    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""), strict=True)
        headers = reader.fieldnames
    except csv.Error as exc:
        raise ReconciliationInputError("The CSV header is invalid.") from exc

    if not headers or any(not header for header in headers):
        raise ReconciliationInputError("The CSV header is missing or invalid.")

    normalized_headers = [header.strip() for header in headers]

    if len(normalized_headers) != len(set(normalized_headers)):
        raise ReconciliationInputError("Duplicate CSV column names are not allowed.")

    if set(normalized_headers) != set(REQUIRED_COLUMNS):
        raise ReconciliationInputError(
            "The CSV must contain exactly these columns: "
            + ", ".join(REQUIRED_COLUMNS)
            + "."
        )

    if headers != normalized_headers:
        raise ReconciliationInputError(
            "CSV column names must not contain surrounding whitespace."
        )

    records: list[dict[str, Any]] = []

    try:
        for row in reader:
            if len(records) >= MAX_RECORDS:
                raise ReconciliationInputError(
                    f"The CSV cannot contain more than {MAX_RECORDS} records."
                )

            if None in row or any(value is None for value in row.values()):
                raise ReconciliationInputError(
                    "A CSV row has missing or extra columns."
                )

            merchant_reference = row["merchant_reference"].strip()
            paypal_order_id = row["paypal_order_id"].strip()
            amount_text = row["expected_amount"].strip()
            currency_code = row["currency_code"].strip().upper()

            if not merchant_reference or not paypal_order_id:
                raise ReconciliationInputError(
                    "Every row must include a merchant reference and PayPal order ID."
                )

            if len(merchant_reference) > 200 or len(paypal_order_id) > 128:
                raise ReconciliationInputError(
                    "A merchant reference or PayPal order ID is too long."
                )

            if not AMOUNT_PATTERN.fullmatch(amount_text):
                raise ReconciliationInputError(
                    "Amounts must be positive numbers with at most two decimal places."
                )

            try:
                amount = Decimal(amount_text)
            except InvalidOperation as exc:
                raise ReconciliationInputError(
                    "An expected amount is invalid."
                ) from exc

            if not amount.is_finite() or amount <= 0:
                raise ReconciliationInputError(
                    "Expected amounts must be finite and greater than zero."
                )

            if not CURRENCY_PATTERN.fullmatch(currency_code):
                raise ReconciliationInputError(
                    "Currency codes must contain exactly three letters."
                )

            records.append(
                {
                    "merchant_reference": merchant_reference,
                    "paypal_order_id": paypal_order_id,
                    "expected_amount": amount,
                    "currency_code": currency_code,
                }
            )
    except csv.Error as exc:
        raise ReconciliationInputError("The CSV rows could not be parsed.") from exc

    if not records:
        raise ReconciliationInputError("The CSV contains no data records.")

    return records


def _needs_review(
    record: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    """Return a review result without asserting a match."""
    return {
        "merchant_reference": record.get("merchant_reference"),
        "paypal_order_id": record.get("paypal_order_id"),
        "status": "needs_review",
        "summary": summary,
        "expected_amount": str(record.get("expected_amount", "")),
        "expected_currency": record.get("currency_code"),
        "observed_amount": None,
        "observed_currency": None,
        "settlement_verified": False,
    }


def compare_reconciliation_record(
    record: dict[str, Any],
    order: Any,
) -> dict[str, Any]:
    """Compare one merchant record with unambiguous PayPal order evidence."""
    if not isinstance(order, dict):
        return _needs_review(record, "The PayPal order response is invalid.")

    order_id = order.get("id")
    if order_id != record.get("paypal_order_id"):
        return _needs_review(
            record,
            "The returned PayPal order ID does not match the requested ID.",
        )

    units = order.get("purchase_units")
    if not isinstance(units, list) or len(units) != 1:
        return _needs_review(
            record,
            "Exactly one purchase unit is required for automatic comparison.",
        )

    unit = units[0]
    if not isinstance(unit, dict):
        return _needs_review(record, "The purchase unit is malformed.")

    amount_data = unit.get("amount")
    if not isinstance(amount_data, dict):
        return _needs_review(record, "The purchase-unit amount is missing.")

    amount_text = amount_data.get("value")
    currency_code = amount_data.get("currency_code")

    if (
        not isinstance(amount_text, str)
        or not AMOUNT_PATTERN.fullmatch(amount_text)
        or not isinstance(currency_code, str)
        or not CURRENCY_PATTERN.fullmatch(currency_code.upper())
    ):
        return _needs_review(
            record,
            "The PayPal amount or currency evidence is malformed.",
        )

    try:
        observed_amount = Decimal(amount_text)
    except InvalidOperation:
        return _needs_review(record, "The PayPal amount is invalid.")

    if not observed_amount.is_finite() or observed_amount <= 0:
        return _needs_review(record, "The PayPal amount is invalid.")

    observed_currency = currency_code.upper()
    expected_amount = record["expected_amount"]
    expected_currency = record["currency_code"]

    if observed_currency != expected_currency:
        status = "currency_mismatch"
        summary = "The PayPal order currency differs from the merchant record."
    elif observed_amount != expected_amount:
        status = "amount_mismatch"
        summary = "The PayPal order amount differs from the merchant record."
    else:
        status = "matched"
        summary = (
            "The order amount and currency match the merchant record. "
            "Settlement is not verified."
        )

    return {
        "merchant_reference": record["merchant_reference"],
        "paypal_order_id": record["paypal_order_id"],
        "status": status,
        "summary": summary,
        "expected_amount": str(expected_amount),
        "expected_currency": expected_currency,
        "observed_amount": str(observed_amount),
        "observed_currency": observed_currency,
        "settlement_verified": False,
    }



def identify_duplicate_records(
    records: list[dict[str, Any]],
) -> dict[int, str]:
    """Flag every row involved in duplicate merchant references or order IDs."""
    occurrences: dict[str, dict[str, list[int]]] = {
        "merchant reference": {},
        "paypal order id": {},
    }

    for index, record in enumerate(records):
        for field, label in (
            ("merchant_reference", "merchant reference"),
            ("paypal_order_id", "paypal order id"),
        ):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                normalized_value = value.strip()
                occurrences[label].setdefault(normalized_value, []).append(index)

    reasons: dict[int, list[str]] = {}

    for label, values in occurrences.items():
        for value, indexes in values.items():
            if len(indexes) > 1:
                for index in indexes:
                    reasons.setdefault(index, []).append(
                        f"Duplicate {label}: {value}."
                    )

    return {
        index: " ".join(messages)
        for index, messages in reasons.items()
    }
