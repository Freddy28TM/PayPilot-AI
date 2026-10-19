"""Integration tests for the merchant reconciliation API."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import (
    app,
    get_paypal_client,
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
def reconciliation_client():
    fake_paypal = FakePayPalClient()
    app.dependency_overrides[get_paypal_client] = lambda: fake_paypal

    try:
        with TestClient(app) as client:
            yield client, fake_paypal
    finally:
        app.dependency_overrides.pop(get_paypal_client, None)


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
    assert response.json()["counts"] == {"needs_review": 2}
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
        asyncio.run(reconcile_merchant_csv(request, FakePayPalClient()))

    assert exc_info.value.status_code == 413
    assert request.chunks_yielded == (
        max_request_bytes // chunk_size
    ) + 1
