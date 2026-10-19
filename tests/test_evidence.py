"""Tests for deterministic PayPal order evidence classification."""

import pytest

from backend.evidence import classify_order_evidence


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("CREATED", "order_created"),
        ("SAVED", "order_saved"),
        ("APPROVED", "order_approved_not_completed"),
        ("PAYER_ACTION_REQUIRED", "payer_action_required"),
        ("COMPLETED", "completed_settlement_unverified"),
        ("VOIDED", "order_voided"),
    ],
)
def test_known_statuses_are_classified(status, expected):
    result = classify_order_evidence({"id": "ORDER-1", "status": status})

    assert result["classification"] == expected
    assert result["observed_status"] == status
    assert result["settlement_verified"] is False


def test_completed_order_does_not_claim_settlement():
    result = classify_order_evidence({"id": "ORDER-2", "status": "COMPLETED"})

    assert result["classification"] == "completed_settlement_unverified"
    assert "not verified" in result["summary"]


@pytest.mark.parametrize(
    "order",
    [
        None,
        [],
        {},
        {"id": "ORDER-3"},
        {"status": "COMPLETED"},
        {"id": " ", "status": "COMPLETED"},
        {"id": "ORDER-4", "status": " "},
        {"id": "ORDER-5", "status": 123},
    ],
)
def test_missing_or_invalid_evidence_is_not_guessed(order):
    result = classify_order_evidence(order)

    assert result["classification"] == "insufficient_evidence"
    assert result["settlement_verified"] is False


def test_unknown_status_is_flagged_for_review():
    result = classify_order_evidence(
        {"id": "ORDER-6", "status": "UNRECOGNIZED_STATUS"}
    )

    assert result["classification"] == "unknown_status_review"
    assert result["settlement_verified"] is False
    assert "Review" in result["next_step"]


def test_status_matching_is_case_insensitive():
    result = classify_order_evidence({"id": "ORDER-7", "status": "completed"})

    assert result["classification"] == "completed_settlement_unverified"
    assert result["observed_status"] == "COMPLETED"

from backend.evidence import assess_capture_evidence


def test_capture_assessment_handles_missing_capture_data():
    result = assess_capture_evidence({})

    assert result["classification"] == "capture_evidence_missing"
    assert result["capture_count"] == 0
    assert result["settlement_verified"] is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("COMPLETED", "captures_completed"),
        ("PENDING", "captures_pending"),
        ("FAILED", "capture_failure_review"),
        ("DECLINED", "capture_failure_review"),
        ("REFUNDED", "refund_activity_review"),
        ("PARTIALLY_REFUNDED", "refund_activity_review"),
    ],
)
def test_capture_statuses_are_classified_without_claiming_settlement(
    status, expected
):
    order = {
        "purchase_units": [
            {
                "payments": {
                    "captures": [{"id": "CAPTURE-1", "status": status}]
                }
            }
        ]
    }

    result = assess_capture_evidence(order)

    assert result["classification"] == expected
    assert result["capture_count"] == 1
    assert result["settlement_verified"] is False


def test_mixed_capture_statuses_review_all_captures():
    order = {
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {"id": "CAPTURE-1", "status": "COMPLETED"},
                        {"id": "CAPTURE-2", "status": "PENDING"},
                    ]
                }
            }
        ]
    }

    result = assess_capture_evidence(order)

    assert result["classification"] == "mixed_capture_outcomes_review"
    assert result["capture_count"] == 2
    assert result["settlement_verified"] is False


@pytest.mark.parametrize(
    "order",
    [
        {"purchase_units": "invalid"},
        {"purchase_units": [{"payments": {"captures": "invalid"}}]},
        {
            "purchase_units": [
                {"payments": {"captures": [{"status": "UNKNOWN"}]}}
            ]
        },
    ],
)
def test_invalid_or_unknown_capture_evidence_is_flagged(order):
    result = assess_capture_evidence(order)

    assert result["classification"] == "capture_evidence_review"
    assert result["settlement_verified"] is False

def test_missing_payments_is_not_treated_as_malformed():
    result = assess_capture_evidence({"purchase_units": [{}]})

    assert result["classification"] == "capture_evidence_missing"
    assert result["capture_count"] == 0
    assert result["settlement_verified"] is False


def test_empty_purchase_units_means_capture_evidence_missing():
    result = assess_capture_evidence({"purchase_units": []})

    assert result["classification"] == "capture_evidence_missing"
    assert result["capture_count"] == 0
    assert result["settlement_verified"] is False


def test_invalid_payments_object_requires_review():
    result = assess_capture_evidence(
        {"purchase_units": [{"payments": "invalid"}]}
    )

    assert result["classification"] == "capture_evidence_review"
    assert result["settlement_verified"] is False
