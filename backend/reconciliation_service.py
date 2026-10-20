"""Orchestrate safe, read-only merchant-to-PayPal reconciliation."""

from typing import Any

from backend.paypal_client import PayPalAPIError
from backend.reconciliation import (
    compare_reconciliation_record,
    identify_duplicate_records,
    parse_reconciliation_csv,
)


def _lookup_error(record: dict[str, Any], summary: str) -> dict[str, Any]:
    """Return a safe result for a failed PayPal lookup."""
    return {
        "merchant_reference": record["merchant_reference"],
        "paypal_order_id": record["paypal_order_id"],
        "status": "lookup_error",
        "summary": summary,
        "expected_amount": str(record["expected_amount"]),
        "expected_currency": record["currency_code"],
        "observed_amount": None,
        "observed_currency": None,
        "settlement_verified": False,
    }


def _order_not_found(record: dict[str, Any]) -> dict[str, Any]:
    """Return a result for a confirmed PayPal 404."""
    return {
        "merchant_reference": record["merchant_reference"],
        "paypal_order_id": record["paypal_order_id"],
        "status": "order_not_found",
        "summary": "PayPal reported that this order was not found.",
        "expected_amount": str(record["expected_amount"]),
        "expected_currency": record["currency_code"],
        "observed_amount": None,
        "observed_currency": None,
        "settlement_verified": False,
    }


def reconcile_records(
    records: list[dict[str, Any]],
    paypal: Any,
) -> dict[str, Any]:
    """Reconcile parsed records without letting one lookup failure stop others."""
    duplicate_reasons = identify_duplicate_records(records)
    results: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        if index in duplicate_reasons:
            result = {
                "merchant_reference": record["merchant_reference"],
                "paypal_order_id": record["paypal_order_id"],
                "status": "needs_review",
                "summary": duplicate_reasons[index],
                "expected_amount": str(record["expected_amount"]),
                "expected_currency": record["currency_code"],
                "observed_amount": None,
                "observed_currency": None,
                "settlement_verified": False,
                "risk_signals": ["duplicate_in_file"],
            }
            results.append(result)
            continue

        try:
            order = paypal.get_order(record["paypal_order_id"])
        except PayPalAPIError as exc:
            if exc.status_code == 404:
                results.append(_order_not_found(record))
            else:
                results.append(
                    _lookup_error(
                        record,
                        "PayPal could not complete this order lookup.",
                    )
                )
            continue
        except RuntimeError as exc:
            if "timed out" in str(exc).lower():
                summary = "The PayPal order lookup timed out."
            else:
                summary = "PayPal is temporarily unavailable for this lookup."

            results.append(_lookup_error(record, summary))
            continue

        results.append(compare_reconciliation_record(record, order))

    counts: dict[str, int] = {}
    for result in results:
        result.setdefault("risk_signals", [])
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

    return {
        "results": results,
        "count": len(results),
        "counts": counts,
        "settlement_verified": False,
    }


def reconcile_csv(csv_text: str, paypal: Any) -> dict[str, Any]:
    """Validate a CSV and reconcile each non-duplicate record."""
    records = parse_reconciliation_csv(csv_text)
    return reconcile_records(records, paypal)
