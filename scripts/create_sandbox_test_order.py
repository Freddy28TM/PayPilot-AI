"""Create a $1.00 PayPal Sandbox order without capturing it."""

import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings
from backend.paypal_client import PayPalClient


def main() -> None:
    settings = Settings()

    if settings.paypal_environment != "sandbox":
        raise SystemExit("STOP: PayPal Sandbox environment is required.")

    with PayPalClient(settings=settings) as paypal:
        order = paypal.create_sandbox_test_order()

    order_id = order["id"]
    links = order["links"]
    approvals = [
        link for link in links
        if isinstance(link, dict) and link.get("rel") == "approve"
    ]

    if len(approvals) != 1:
        raise SystemExit(
            f"Order {order_id} was created, but exactly one approval link "
            "was not returned. Check the Sandbox account before retrying."
        )

    approval_url = approvals[0].get("href")
    parsed = urlparse(approval_url) if isinstance(approval_url, str) else None

    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname not in {
            "sandbox.paypal.com",
            "www.sandbox.paypal.com",
        }
    ):
        raise SystemExit(
            f"Order {order_id} was created, but its approval URL failed "
            "validation. Do not open it."
        )

    print(f"Sandbox order ID: {order_id}")
    print(f"Approval URL: {approval_url}")
    print("No capture request was made.")


if __name__ == "__main__":
    main()
