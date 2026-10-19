"""Tests for the read-only PayPilot AI API."""

import pytest
from fastapi.testclient import TestClient

from backend.main import (
    app,
    get_gemini_explainer,
    get_paypal_client,
)
from backend.paypal_client import PayPalAPIError


class FakePayPalClient:
    """Fake PayPal client that never makes network requests."""

    def __init__(self):
        self.order = {
            "id": "TEST-ORDER",
            "status": "COMPLETED",
        }
        self.error = None
        self.requested_order_id = None

    def get_order(self, order_id):
        self.requested_order_id = order_id
        if self.error is not None:
            raise self.error
        return self.order



class FakeGeminiExplainer:
    """Deterministic fake; never contacts the Gemini service."""

    def explain(self, evidence, assessment):
        return {
            "status": "fallback",
            "explanation": assessment["summary"],
        }


@pytest.fixture
def api_client():
    fake_paypal = FakePayPalClient()
    app.dependency_overrides[get_paypal_client] = lambda: fake_paypal
    fake_explainer = FakeGeminiExplainer()
    app.dependency_overrides[get_gemini_explainer] = (
        lambda: fake_explainer
    )

    try:
        with TestClient(app) as client:
            yield client, fake_paypal
    finally:
        app.dependency_overrides.clear()


def test_homepage_serves_frontend(api_client):
    client, _ = api_client

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PayPilot AI" in response.text
    assert 'id="investigation-form"' in response.text


def test_health_endpoint(api_client):
    client, _ = api_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "sandbox",
    }


def test_order_endpoint_returns_paypal_evidence(api_client):
    client, fake_paypal = api_client

    response = client.get("/api/paypal/orders/TEST-ORDER")

    assert response.status_code == 200
    assert response.json() == {
        "id": "TEST-ORDER",
        "status": "COMPLETED",
    }
    assert fake_paypal.requested_order_id == "TEST-ORDER"


def test_unknown_order_returns_404(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = PayPalAPIError(404, "safe test error")

    response = client.get("/api/paypal/orders/UNKNOWN-ORDER")

    assert response.status_code == 404
    assert response.json()["detail"] == "PayPal order was not found."


def test_upstream_api_failure_returns_502(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = PayPalAPIError(500, "safe test error")

    response = client.get("/api/paypal/orders/TEST-ORDER")

    assert response.status_code == 502
    assert "safe test error" not in response.text


def test_timeout_returns_504(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = RuntimeError("PayPal API request timed out.")

    response = client.get("/api/paypal/orders/TEST-ORDER")

    assert response.status_code == 504
    assert response.json()["detail"] == "PayPal order lookup timed out."


def test_invalid_order_response_returns_502(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = RuntimeError(
        "PayPal returned an invalid order response."
    )

    response = client.get("/api/paypal/orders/TEST-ORDER")

    assert response.status_code == 502
    assert "invalid order response" not in response.text


def test_whitespace_order_id_returns_422(api_client):
    client, fake_paypal = api_client

    response = client.get("/api/paypal/orders/%20%20%20")

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "A non-empty PayPal order ID is required."
    )
    assert fake_paypal.requested_order_id is None


def test_overlong_order_id_is_rejected(api_client):
    client, fake_paypal = api_client

    response = client.get(f"/api/paypal/orders/{'A' * 129}")

    assert response.status_code == 422
    assert fake_paypal.requested_order_id is None


def test_investigation_endpoint_returns_assessment(api_client):
    client, fake_paypal = api_client

    response = client.get(
        "/api/paypal/orders/TEST-ORDER/investigation"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"] == {"id": "TEST-ORDER", "status": "COMPLETED"}
    assert body["capture_assessment"]["classification"] == "capture_evidence_missing"
    assert body["capture_assessment"]["settlement_verified"] is False
    assert body["assessment"]["classification"] == (
        "completed_settlement_unverified"
    )
    assert body["assessment"]["settlement_verified"] is False
    assert body["explanation"]["status"] == "fallback"
    assert body["explanation"]["explanation"] == (
        body["assessment"]["summary"]
    )
    assert fake_paypal.requested_order_id == "TEST-ORDER"


def test_investigation_endpoint_maps_unknown_order_to_404(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = PayPalAPIError(404, "safe test error")

    response = client.get(
        "/api/paypal/orders/UNKNOWN-ORDER/investigation"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "PayPal order was not found."


def test_investigation_endpoint_does_not_leak_upstream_error(api_client):
    client, fake_paypal = api_client
    fake_paypal.error = PayPalAPIError(500, "private upstream detail")

    response = client.get(
        "/api/paypal/orders/TEST-ORDER/investigation"
    )

    assert response.status_code == 502
    assert "private upstream detail" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/paypal/orders/TEST-ORDER",
        "/api/paypal/orders/TEST-ORDER/investigation",
    ],
)
def test_order_responses_exclude_unneeded_payer_fields(api_client, path):
    client, fake_paypal = api_client
    fake_paypal.order.update(
        {
            "payer": {
                "email_address": "synthetic-payer@example.test",
                "name": {"given_name": "Synthetic"},
            },
            "internal_test_field": "must-not-be-returned",
        }
    )

    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    returned_evidence = (
        body["evidence"] if path.endswith("/investigation") else body
    )
    assert returned_evidence == {
        "id": "TEST-ORDER",
        "status": "COMPLETED",
    }
    assert "synthetic-payer@example.test" not in response.text
    assert "must-not-be-returned" not in response.text
