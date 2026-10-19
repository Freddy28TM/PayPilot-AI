"""Deterministic interpretation of PayPal order evidence.

Order status alone is not proof of merchant settlement.
This module performs no network or AI operations.
"""

from typing import Any

_ORDER_STATUS_RULES: dict[str, tuple[str, str, str]] = {
    "CREATED": (
        "order_created",
        "Order created but not shown as completed.",
        "Retrieve updated order evidence before drawing further conclusions.",
    ),
    "SAVED": (
        "order_saved",
        "Order saved; completion is not established.",
        "Check the order workflow and retrieve updated evidence.",
    ),
    "APPROVED": (
        "order_approved_not_completed",
        "Approval is recorded, but order completion is not established.",
        "Check the authorized checkout workflow for the next required step.",
    ),
    "PAYER_ACTION_REQUIRED": (
        "payer_action_required",
        "The order indicates that further payer action is required.",
        "Review the checkout flow and request the required action through the normal process.",
    ),
    "COMPLETED": (
        "completed_settlement_unverified",
        "The order is completed, but settlement is not verified by this evidence alone.",
        "Check appropriate capture and transaction reporting evidence before confirming settlement.",
    ),
    "VOIDED": (
        "order_voided",
        "The order is voided.",
        "Review the order history if the void was unexpected.",
    ),
}


def select_order_evidence(order: Any) -> dict[str, Any]:
    """Expose only the order identity and status needed for investigation."""
    if not isinstance(order, dict):
        return {}

    return {
        key: order[key]
        for key in ("id", "status")
        if key in order
    }


def classify_order_evidence(order: Any) -> dict[str, Any]:
    """Classify one order response without inferring settlement."""
    if not isinstance(order, dict):
        return {
            "classification": "insufficient_evidence",
            "observed_status": None,
            "settlement_verified": False,
            "summary": "The order response is missing or invalid.",
            "next_step": "Retrieve a valid PayPal order response.",
        }

    order_id = order.get("id")
    raw_status = order.get("status")

    if (
        not isinstance(order_id, str)
        or not order_id.strip()
        or not isinstance(raw_status, str)
        or not raw_status.strip()
    ):
        return {
            "classification": "insufficient_evidence",
            "observed_status": None,
            "settlement_verified": False,
            "summary": "Required order identity or status evidence is missing.",
            "next_step": "Retrieve a complete PayPal order response.",
        }

    status = raw_status.strip().upper()
    rule = _ORDER_STATUS_RULES.get(status)

    if rule is None:
        return {
            "order_id": order_id,
            "classification": "unknown_status_review",
            "observed_status": status,
            "settlement_verified": False,
            "summary": "The order status is not recognized by the current rules.",
            "next_step": "Review the status against the current PayPal API documentation.",
        }

    classification, summary, next_step = rule

    return {
        "order_id": order_id,
        "classification": classification,
        "observed_status": status,
        "settlement_verified": False,
        "summary": summary,
        "next_step": next_step,
    }

def assess_capture_evidence(order: Any) -> dict[str, Any]:
    """Assess capture statuses without claiming bank settlement."""
    review = {
        "classification": "capture_evidence_review",
        "capture_count": 0,
        "settlement_verified": False,
        "summary": "Capture evidence is malformed or contains an unknown status.",
        "next_step": "Review the PayPal capture response and current API documentation.",
    }

    if not isinstance(order, dict):
        return review

    if "purchase_units" not in order:
        return {
            "classification": "capture_evidence_missing",
            "capture_count": 0,
            "settlement_verified": False,
            "summary": "No capture records were found in the supplied order evidence.",
            "next_step": "Retrieve the relevant capture or transaction evidence.",
        }
    units = order["purchase_units"]
    if not isinstance(units, list):
        return review

    captures: list[dict[str, Any]] = []

    for unit in units:
        if not isinstance(unit, dict):
            return review

        if "payments" not in unit:
            continue

        payments = unit["payments"]
        if not isinstance(payments, dict):
            return review

        if "captures" not in payments:
            continue

        unit_captures = payments["captures"]
        if not isinstance(unit_captures, list):
            return review

        for capture in unit_captures:
            if not isinstance(capture, dict):
                return review
            status = capture.get("status")
            if not isinstance(status, str) or not status.strip():
                return review
            normalized = status.strip().upper()
            if normalized not in {
                "COMPLETED", "PENDING", "FAILED", "DECLINED",
                "REFUNDED", "PARTIALLY_REFUNDED",
            }:
                return review
            captures.append({"status": normalized})

    count = len(captures)
    statuses = [capture["status"] for capture in captures]

    if count == 0:
        return {
            "classification": "capture_evidence_missing",
            "capture_count": 0,
            "settlement_verified": False,
            "summary": "No capture records were found in the supplied order evidence.",
            "next_step": "Retrieve the relevant capture or transaction evidence.",
        }

    if len(set(statuses)) > 1:
        classification = "mixed_capture_outcomes_review"
        summary = "Capture records have different statuses and need review."
        next_step = "Review each capture and its transaction history."
    elif statuses[0] == "COMPLETED":
        classification = "captures_completed"
        summary = "Capture status is completed; bank settlement is not verified."
        next_step = "Check appropriate transaction or settlement reporting evidence."
    elif statuses[0] == "PENDING":
        classification = "captures_pending"
        summary = "Capture status is pending."
        next_step = "Check the capture again later and review updated transaction evidence."
    elif statuses[0] in {"FAILED", "DECLINED"}:
        classification = "capture_failure_review"
        summary = "Capture evidence reports a failed or declined outcome."
        next_step = "Review the capture details and related PayPal transaction evidence."
    else:
        classification = "refund_activity_review"
        summary = "Capture evidence indicates refund activity."
        next_step = "Review the capture and associated refund transaction records."

    return {
        "classification": classification,
        "capture_count": count,
        "settlement_verified": False,
        "summary": summary,
        "next_step": next_step,
    }
