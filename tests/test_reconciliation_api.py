"""Integration tests for the merchant reconciliation API."""

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.database import ReconciliationRepository
from backend.main import (
    app,
    get_paypal_client,
    get_reconciliation_repository,
    reconcile_merchant_csv,
)
from backend.paypal_client import PayPalAPIError
from backend.reconciliation import MAX_CSV_BYTES


class FakePayPalClient:
    """Fake PayPal client that never makes network requests."""

    def __init__(self):
        self.orders = {
            "ORDER-1": {
                "id": "ORDER-1",
                "purchase_units": [
                    {"amount": {"value": "25.00", "currency_code": "USD"}}
                ],
            },
        }
        self.errors = {}
        self.requested_ids = []

    def get_order(self, order_id):
        self.requested_ids.append(order_id)
        if order_id in self.errors:
            raise self.errors[order_id]
        if order_id not in self.orders:
            raise PayPalAPIError(404, "safe test error")
        return self.orders[order_id]


@pytest.fixture
def reconciliation_client(tmp_path):
    fake_paypal = FakePayPalClient()
    repository = ReconciliationRepository(
        database_path=tmp_path / "reconciliation-test.sqlite3"
    )
    repository.initialize()

    app.dependency_overrides[get_paypal_client] = lambda: fake_paypal
    app.dependency_overrides[get_reconciliation_repository] = lambda: repository

    try:
        with TestClient(app) as client:
            yield client, fake_paypal
    finally:
        app.dependency_overrides.pop(get_paypal_client, None)
        app.dependency_overrides.pop(get_reconciliation_repository, None)


def csv_body(rows):
    return {
        "csv_text": (
            "merchant_reference,paypal_order_id,expected_amount,currency_code\n"
            + rows
        )
    }


def test_matching_order_returns_matched_without_verifying_settlement(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json=csv_body("INV-1,ORDER-1,25.00,USD\n"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["counts"] == {"matched": 1}
    assert body["results"][0]["status"] == "matched"
    assert body["results"][0]["observed_amount"] == "25.00"
    assert body["results"][0]["observed_currency"] == "USD"
    assert body["settlement_verified"] is False
    assert body["results"][0]["settlement_verified"] is False
    assert fake_paypal.requested_ids == ["ORDER-1"]


@pytest.mark.parametrize(
    ("expected_amount", "currency", "expected_status"),
    [
        ("26.00", "USD", "amount_mismatch"),
        ("25.00", "KES", "currency_mismatch"),
    ],
)
def test_amount_and_currency_mismatches_are_reported(
    reconciliation_client, expected_amount, currency, expected_status
):
    client, _ = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json=csv_body(f"INV-1,ORDER-1,{expected_amount},{currency}\n"),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == expected_status
    assert response.json()["settlement_verified"] is False


def test_duplicate_order_ids_are_flagged_without_paypal_lookup(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json=csv_body(
            "INV-1,ORDER-1,25.00,USD\n"
            "INV-2,ORDER-1,25.00,USD\n"
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["counts"] == {"needs_review": 2}
    assert all(row["status"] == "needs_review" for row in body["results"])
    assert all(
        row["risk_signals"] == ["duplicate_in_file"]
        for row in body["results"]
    )
    assert body["settlement_verified"] is False
    assert fake_paypal.requested_ids == []


def test_duplicate_merchant_references_are_flagged(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client
    fake_paypal.orders["ORDER-2"] = {
        "id": "ORDER-2",
        "purchase_units": [
            {"amount": {"value": "40.00", "currency_code": "USD"}}
        ],
    }

    response = client.post(
        "/api/reconciliation",
        json=csv_body(
            "INV-1,ORDER-1,25.00,USD\n"
            "INV-1,ORDER-2,40.00,USD\n"
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"needs_review": 2}
    assert all(
        row["risk_signals"] == ["duplicate_in_file"]
        for row in body["results"]
    )
    assert fake_paypal.requested_ids == []


def test_paypal_404_is_distinguished_from_upstream_failure(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client
    fake_paypal.errors["MISSING"] = PayPalAPIError(404, "private upstream detail")
    fake_paypal.errors["BROKEN"] = PayPalAPIError(503, "private upstream detail")

    response = client.post(
        "/api/reconciliation",
        json=csv_body(
            "INV-1,MISSING,25.00,USD\n"
            "INV-2,BROKEN,25.00,USD\n"
            "INV-3,ORDER-1,25.00,USD\n"
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["status"] for row in body["results"]] == [
        "order_not_found",
        "lookup_error",
        "matched",
    ]
    assert "private upstream detail" not in response.text
    assert body["settlement_verified"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"csv_text": 123},
        {"csv_text": "merchant_reference,paypal_order_id,expected_amount,currency_code\nINV-1,ORDER-1,25.00,USD\n", "extra": True},
    ],
)
def test_invalid_request_shapes_are_rejected(reconciliation_client, payload):
    client, fake_paypal = reconciliation_client

    response = client.post("/api/reconciliation", json=payload)

    assert response.status_code == 422
    assert fake_paypal.requested_ids == []


def test_invalid_csv_is_rejected(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json={"csv_text": "not,the,required,headers\n"},
    )

    assert response.status_code == 422
    assert fake_paypal.requested_ids == []


def test_malformed_json_is_rejected(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        content=b'{"csv_text":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert fake_paypal.requested_ids == []


def test_csv_over_one_megabyte_is_rejected(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json={"csv_text": "x" * 1_000_001},
    )

    assert response.status_code == 413
    assert fake_paypal.requested_ids == []


def test_oversized_stream_without_content_length_is_rejected():
    max_request_bytes = (MAX_CSV_BYTES * 2) + 4096
    chunk_size = 65536

    class StreamingRequest:
        def __init__(self):
            self.headers = {}
            self.chunks_yielded = 0

        async def stream(self):
            while True:
                self.chunks_yielded += 1
                yield b"x" * chunk_size

    request = StreamingRequest()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            reconcile_merchant_csv(
                request,
                FakePayPalClient(),
                ReconciliationRepository(),
            )
        )

    assert exc_info.value.status_code == 413
    assert request.chunks_yielded == (
        max_request_bytes // chunk_size
    ) + 1


def test_first_reconciliation_has_no_historical_matches(reconciliation_client):
    client, _ = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json=csv_body("INV-FIRST,ORDER-1,25.00,USD\n"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["historical_matches"] == []
    assert body["results"][0]["risk_signals"] == []
    assert body["counts"] == {"matched": 1}
    assert body["results"][0]["status"] == "matched"
    assert body["settlement_verified"] is False


def test_cross_run_matches_are_informational_and_do_not_change_counts(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client
    fake_paypal.orders["ORDER-2"] = {
        "id": "ORDER-2",
        "purchase_units": [
            {"amount": {"value": "25.00", "currency_code": "USD"}}
        ],
    }

    first = client.post(
        "/api/reconciliation",
        json=csv_body("INV-REPEAT,ORDER-1,25.00,USD\n"),
    )
    assert first.status_code == 200
    first_run_id = first.json()["run_id"]
    assert first.json()["results"][0]["historical_matches"] == []

    second = client.post(
        "/api/reconciliation",
        json=csv_body("INV-REPEAT,ORDER-2,25.00,USD\n"),
    )
    assert second.status_code == 200
    second_body = second.json()
    second_row = second_body["results"][0]

    assert second_row["status"] == "matched"
    assert second_row["risk_signals"] == ["historical_identifier_match"]
    assert second_body["counts"] == {"matched": 1}
    assert second_row["settlement_verified"] is False
    assert second_body["settlement_verified"] is False
    assert second_row["historical_matches"] == [
        {
            "match_type": "merchant_reference",
            "run_id": first_run_id,
            "row_number": 1,
            "matched_value": "INV-REPEAT",
        }
    ]

    third = client.post(
        "/api/reconciliation",
        json=csv_body("INV-NEW,ORDER-2,25.00,USD\n"),
    )
    assert third.status_code == 200
    third_row = third.json()["results"][0]

    assert third_row["status"] == "matched"
    assert third_row["risk_signals"] == ["historical_identifier_match"]
    assert third.json()["counts"] == {"matched": 1}
    assert third_row["settlement_verified"] is False
    assert [
        {
            "match_type": item["match_type"],
            "run_id": item["run_id"],
            "row_number": item["row_number"],
            "matched_value": item["matched_value"],
        }
        for item in third_row["historical_matches"]
    ] == [
        {
            "match_type": "paypal_order_id",
            "run_id": second_body["run_id"],
            "row_number": 1,
            "matched_value": "ORDER-2",
        }
    ]

    history = client.get("/api/reconciliation/runs").json()
    assert history["count"] == 3
    assert all(run["settlement_verified"] is False for run in history["runs"])


def test_conflicting_historical_identifiers_are_informational(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client
    fake_paypal.orders["ORDER-2"] = {
        "id": "ORDER-2",
        "purchase_units": [
            {"amount": {"value": "25.00", "currency_code": "USD"}}
        ],
    }

    first = client.post(
        "/api/reconciliation",
        json=csv_body("INV-CONFLICT,ORDER-1,25.00,USD\n"),
    )
    assert first.status_code == 200
    first_run_id = first.json()["run_id"]

    second = client.post(
        "/api/reconciliation",
        json=csv_body("INV-SECOND,ORDER-2,25.00,USD\n"),
    )
    assert second.status_code == 200
    second_run_id = second.json()["run_id"]

    current = client.post(
        "/api/reconciliation",
        json=csv_body("INV-CONFLICT,ORDER-2,25.00,USD\n"),
    )

    assert current.status_code == 200
    body = current.json()
    row = body["results"][0]

    assert row["status"] == "matched"
    assert row["risk_signals"] == ["historical_identifier_match"]
    assert body["counts"] == {"matched": 1}
    assert row["settlement_verified"] is False
    assert body["settlement_verified"] is False
    assert row["historical_matches"] == [
        {
            "match_type": "paypal_order_id",
            "run_id": second_run_id,
            "row_number": 1,
            "matched_value": "ORDER-2",
        },
        {
            "match_type": "merchant_reference",
            "run_id": first_run_id,
            "row_number": 1,
            "matched_value": "INV-CONFLICT",
        },
    ]

def test_successful_run_can_be_listed_and_retrieved_without_paypal_calls(
    reconciliation_client,
):
    client, fake_paypal = reconciliation_client

    response = client.post(
        "/api/reconciliation",
        json=csv_body("INV-HISTORY,ORDER-1,25.00,USD\n"),
    )
    assert response.status_code == 200
    saved = response.json()
    run_id = saved["run_id"]
    calls_after_reconciliation = list(fake_paypal.requested_ids)

    history_response = client.get("/api/reconciliation/runs")
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["count"] == 1
    assert history["runs"][0]["run_id"] == run_id
    assert history["runs"][0]["settlement_verified"] is False

    detail_response = client.get(f"/api/reconciliation/runs/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["run_id"] == run_id
    assert detail["record_count"] == 1
    assert detail["results"][0]["merchant_reference"] == "INV-HISTORY"
    assert detail["results"][0]["status"] == "matched"
    assert detail["settlement_verified"] is False
    assert detail["results"][0]["settlement_verified"] is False
    assert fake_paypal.requested_ids == calls_after_reconciliation


def test_unknown_reconciliation_run_returns_404(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.get("/api/reconciliation/runs/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Reconciliation run was not found."
    assert fake_paypal.requested_ids == []


def test_reconciliation_history_limit_is_validated(reconciliation_client):
    client, _ = reconciliation_client

    assert client.get("/api/reconciliation/runs?limit=0").status_code == 422
    assert client.get("/api/reconciliation/runs?limit=501").status_code == 422

class BrokenReconciliationRepository:
    """Simulate storage failures without touching a real database."""

    def find_previous_matches(self, *, merchant_reference, paypal_order_id):
        raise sqlite3.OperationalError("private database path")

    def save_run(self, result):
        raise sqlite3.OperationalError("private database path")

    def list_runs(self, limit=50):
        raise sqlite3.OperationalError("private database path")

    def get_run(self, run_id):
        raise sqlite3.OperationalError("private database path")


def test_reconciliation_storage_failures_return_safe_503(
    reconciliation_client,
):
    client, _ = reconciliation_client
    app.dependency_overrides[get_reconciliation_repository] = (
        lambda: BrokenReconciliationRepository()
    )

    response = client.post(
        "/api/reconciliation",
        json=csv_body("INV-FAIL,ORDER-1,25.00,USD\n"),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reconciliation history is temporarily unavailable."
    )
    assert "private database path" not in response.text


def test_listing_history_storage_failure_returns_safe_503(
    reconciliation_client,
):
    client, _ = reconciliation_client
    app.dependency_overrides[get_reconciliation_repository] = (
        lambda: BrokenReconciliationRepository()
    )

    response = client.get("/api/reconciliation/runs")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reconciliation history is temporarily unavailable."
    )
    assert "private database path" not in response.text


def test_retrieving_history_storage_failure_returns_safe_503(
    reconciliation_client,
):
    client, _ = reconciliation_client
    app.dependency_overrides[get_reconciliation_repository] = (
        lambda: BrokenReconciliationRepository()
    )

    response = client.get("/api/reconciliation/runs/1")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reconciliation history is temporarily unavailable."
    )
    assert "private database path" not in response.text
def test_analytics_summary_api_returns_empty_history(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.get("/api/analytics/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_runs"] == 0
    assert body["total_rows"] == 0
    assert body["recorded_rows"] == 0
    assert body["record_count_consistent"] is True
    assert body["matched_rows"] == 0
    assert body["exception_rows"] == 0
    assert body["match_rate_percent"] == 0.0
    assert body["latest_run_at"] is None
    assert body["settlement_verified"] is False
    assert body["risk_signal_history_available"] is False
    assert fake_paypal.requested_ids == []


def test_analytics_trends_api_returns_empty_history(reconciliation_client):
    client, fake_paypal = reconciliation_client

    response = client.get("/api/analytics/trends")

    assert response.status_code == 200
    assert response.json() == {
        "days": [],
        "count": 0,
        "limit": 30,
        "settlement_verified": False,
    }
    assert fake_paypal.requested_ids == []


def test_analytics_api_reflects_saved_reconciliation(reconciliation_client):
    client, fake_paypal = reconciliation_client

    reconciliation = client.post(
        "/api/reconciliation",
        json=csv_body(
            "INV-ANALYTICS-1,ORDER-1,25.00,USD\n"
            "INV-ANALYTICS-2,MISSING-ORDER,25.00,USD\n"
        ),
    )
    assert reconciliation.status_code == 200

    summary_response = client.get("/api/analytics/summary")
    trends_response = client.get("/api/analytics/trends?limit=30")

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_runs"] == 1
    assert summary["total_rows"] == 2
    assert summary["recorded_rows"] == 2
    assert summary["record_count_consistent"] is True
    assert summary["matched_rows"] == 1
    assert summary["exception_rows"] == 1
    assert summary["match_rate_percent"] == 50.0
    assert summary["settlement_verified"] is False

    assert trends_response.status_code == 200
    trends = trends_response.json()
    assert trends["limit"] == 30
    assert trends["count"] == 1
    assert trends["settlement_verified"] is False
    assert trends["days"][0]["total_runs"] == 1
    assert trends["days"][0]["total_rows"] == 2
    assert trends["days"][0]["matched_rows"] == 1
    assert trends["days"][0]["exception_rows"] == 1
    assert trends["days"][0]["lookup_failure_rows"] == 1

    # Analytics must read saved data without making extra PayPal requests.
    assert fake_paypal.requested_ids == ["ORDER-1", "MISSING-ORDER"]


@pytest.mark.parametrize("limit", ["0", "-1", "366", "abc"])
def test_analytics_trends_api_rejects_invalid_limits(
    reconciliation_client, limit
):
    client, fake_paypal = reconciliation_client

    response = client.get(f"/api/analytics/trends?limit={limit}")

    assert response.status_code == 422
    assert fake_paypal.requested_ids == []


def test_analytics_summary_storage_failure_returns_safe_503(
    reconciliation_client, monkeypatch
):
    import backend.main as main_module

    client, _ = reconciliation_client

    class BrokenAnalytics:
        def __init__(self, database_path):
            raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(main_module, "ReconciliationAnalytics", BrokenAnalytics)

    response = client.get("/api/analytics/summary")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reconciliation analytics are temporarily unavailable."
    )
    assert "private database path" not in response.text


def test_analytics_trends_storage_failure_returns_safe_503(
    reconciliation_client, monkeypatch
):
    import backend.main as main_module

    client, _ = reconciliation_client

    class BrokenAnalytics:
        def __init__(self, database_path):
            raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(main_module, "ReconciliationAnalytics", BrokenAnalytics)

    response = client.get("/api/analytics/trends?limit=30")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reconciliation analytics are temporarily unavailable."
    )
    assert "private database path" not in response.text
