"""Safe PayPal Sandbox API client."""

from time import monotonic
from typing import Any, Self

import httpx

from backend.config import Settings, get_settings

SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"


class PayPalAPIError(RuntimeError):
    """A safe error for PayPal authentication or API failures."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class PayPalClient:
    """Small reusable client for server-side PayPal API requests."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.settings = settings or get_settings()

        if self.settings.paypal_environment != "sandbox":
            raise ValueError("Only PayPal Sandbox is allowed during setup.")

        self.base_url = SANDBOX_API_BASE
        self._http = http_client or httpx.Client(timeout=20.0)
        self._owns_http = http_client is None
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _credentials(self) -> tuple[str, str]:
        client_id = self.settings.paypal_client_id
        client_secret = self.settings.paypal_client_secret

        if not client_id or not client_secret:
            raise RuntimeError("PayPal Sandbox credentials are not configured.")

        return (
            client_id.get_secret_value(),
            client_secret.get_secret_value(),
        )

    def get_access_token(self) -> str:
        """Return a cached token or request a new Sandbox token."""
        if self._access_token and monotonic() < self._token_expires_at:
            return self._access_token

        try:
            response = self._http.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=self._credentials(),
                headers={"Accept": "application/json"},
                data={"grant_type": "client_credentials"},
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("PayPal authentication timed out.") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(
                "Could not connect to PayPal for authentication."
            ) from exc

        if response.status_code != 200:
            raise PayPalAPIError(
                response.status_code,
                "PayPal Sandbox authentication failed.",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "PayPal returned an invalid authentication response."
            ) from exc

        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 300)

        if not isinstance(token, str) or not token:
            raise RuntimeError("PayPal did not return an access token.")

        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = 300

        self._access_token = token
        self._token_expires_at = monotonic() + max(1, expires_in - 60)
        return token

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make an authenticated API request and return decoded JSON."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("PayPal API path must start with a single slash.")

        token = self.get_access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")

        try:
            response = self._http.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("PayPal API request timed out.") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("Could not connect to the PayPal API.") from exc

        if not 200 <= response.status_code < 300:
            raise PayPalAPIError(
                response.status_code,
                "PayPal API request failed.",
            )

        if response.status_code == 204 or not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("PayPal returned an invalid JSON response.") from exc

    def create_sandbox_test_order(self) -> dict[str, Any]:
        """Create a fixed-value Sandbox test order without capturing it."""
        result = self.request(
            "POST",
            "/v2/checkout/orders",
            headers={"Prefer": "return=representation"},
            json={
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
            },
        )

        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), str)
            or not result["id"].strip()
        ):
            raise RuntimeError(
                "PayPal returned an invalid order creation response."
            )

        links = result.get("links")
        has_approval_link = (
            isinstance(links, list)
            and any(
                isinstance(link, dict)
                and link.get("rel") == "approve"
                and isinstance(link.get("href"), str)
                and bool(link["href"].strip())
                for link in links
            )
        )

        if not has_approval_link:
            raise RuntimeError(
                "PayPal returned no valid order links."
            )

        return result

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Retrieve one PayPal Sandbox order without changing its state."""
        from urllib.parse import quote

        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("A non-empty PayPal order ID is required.")

        encoded_order_id = quote(order_id, safe="")
        result = self.request(
            "GET",
            f"/v2/checkout/orders/{encoded_order_id}",
        )

        if not isinstance(result, dict) or not result:
            raise RuntimeError("PayPal returned an invalid order response.")

        return result

    def close(self) -> None:
        """Close the HTTP connection if this client created it."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
