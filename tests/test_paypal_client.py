"""Tests for the PayPal client using mocked HTTP responses."""

import httpx
import pytest

from backend.config import Settings
from backend.paypal_client import PayPalAPIError, PayPalClient


def make_settings(**overrides):
    values = {
        "_env_file": None,
        "paypal_environment": "sandbox",
        "paypal_client_id": "test-client-id",
        "paypal_client_secret": "test-client-secret",
    }
    values.update(overrides)
    return Settings(**values)


def test_authentication_token_is_cached():
    calls = {"token": 0, "api": 0}

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            calls["token"] += 1
            return httpx.Response(
                200,
                json={"access_token": "synthetic-token", "expires_in": 3600},
            )

        calls["api"] += 1
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"status": "COMPLETED"})

    transport = httpx.MockTransport(handler)

    with (
        httpx.Client(transport=transport) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
    ):
        first = paypal.get_access_token()
        second = paypal.get_access_token()
        result = paypal.request("GET", "/v2/checkout/orders/TEST-ORDER")

    assert first == second == "synthetic-token"
    assert result == {"status": "COMPLETED"}
    assert calls == {"token": 1, "api": 1}


def test_authentication_failure_does_not_expose_response_body():
    def handler(request):
        return httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "sensitive detail"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
        pytest.raises(PayPalAPIError) as exc,
    ):
        paypal.get_access_token()

    assert exc.value.status_code == 401
    assert "sensitive detail" not in str(exc.value)


def test_api_error_reports_status_without_response_body():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "synthetic-token", "expires_in": 3600},
            )
        return httpx.Response(
            500,
            json={"message": "private diagnostic detail"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
        pytest.raises(PayPalAPIError) as exc,
    ):
        paypal.request("GET", "/v2/example")

    assert exc.value.status_code == 500
    assert "private diagnostic detail" not in str(exc.value)


def test_authentication_timeout_is_reported_safely():
    def handler(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
        pytest.raises(RuntimeError, match="authentication timed out"),
    ):
        paypal.get_access_token()


def test_invalid_api_path_is_rejected():
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            )
        ) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
        pytest.raises(ValueError, match="single slash"),
    ):
        paypal.request("GET", "//example.com/path")


def test_missing_credentials_are_rejected():
    settings = make_settings(
        paypal_client_id=None,
        paypal_client_secret=None,
    )

    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            )
        ) as http,
        PayPalClient(settings=settings, http_client=http) as paypal,
        pytest.raises(RuntimeError, match="not configured"),
    ):
        paypal.get_access_token()

def test_create_sandbox_test_order_posts_fixed_payload_without_capture():
    import json

    api_requests = []

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "synthetic-token", "expires_in": 3600},
            )

        api_requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "SYNTHETIC-ORDER-123",
                "status": "CREATED",
                "links": [
                    {
                        "rel": "approve",
                        "href": "https://example.test/approve",
                        "method": "GET",
                    }
                ],
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
    ):
        result = paypal.create_sandbox_test_order()

    assert result["id"] == "SYNTHETIC-ORDER-123"
    assert len(api_requests) == 1

    request = api_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v2/checkout/orders"
    assert request.headers["Prefer"] == "return=representation"
    assert json.loads(request.content) == {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "PAYPILOT-SANDBOX-TEST",
                "description": "PayPilot AI Sandbox test order",
                "amount": {
                    "currency_code": "USD",
                    "value": "1.00",
                },
            }
        ],
    }

    # Only the order-creation request was made; no capture endpoint was called.
    assert all("/capture" not in item.url.path for item in api_requests)


@pytest.mark.parametrize(
    ("response_body", "error_message"),
    [
        (
            {"links": []},
            "invalid order creation response",
        ),
        (
            {"id": "SYNTHETIC-ORDER-123"},
            "no valid order links",
        ),
    ],
)
def test_create_sandbox_test_order_rejects_invalid_response(
    response_body, error_message
):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "synthetic-token", "expires_in": 3600},
            )

        return httpx.Response(201, json=response_body)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        PayPalClient(settings=make_settings(), http_client=http) as paypal,
        pytest.raises(RuntimeError, match=error_message),
    ):
        paypal.create_sandbox_test_order()
