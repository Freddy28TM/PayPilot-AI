"""Local persistence for minimal PayPilot investigation history."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_ORDER_CLASSIFICATIONS = frozenset({
    "completed_settlement_unverified",
    "insufficient_evidence",
    "order_approved_not_completed",
    "order_created",
    "order_saved",
    "order_voided",
    "payer_action_required",
    "unknown_status_review",
})

DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "paypilot.sqlite3"
)


class InvestigationRepository:
    """Store minimal historical snapshots of PayPal investigations."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        """Create the database directory and history table if needed."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    observed_status TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    settlement_verified INTEGER NOT NULL
                        CHECK (settlement_verified = 0),
                    investigated_at TEXT NOT NULL
                )
                """
            )

    def save_investigation(
        self,
        evidence: dict[str, Any],
        assessment: dict[str, Any],
    ) -> int:
        """Persist a validated minimal snapshot and return its local ID."""
        order_id = evidence.get("id")
        status = evidence.get("status")
        classification = assessment.get("classification")
        settlement_verified = assessment.get("settlement_verified")

        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("A valid PayPal order ID is required.")

        if not isinstance(status, str) or not status.strip():
            raise ValueError("A valid observed order status is required.")

        if (
            not isinstance(classification, str)
            or classification not in ALLOWED_ORDER_CLASSIFICATIONS
        ):
            raise ValueError("A recognized assessment classification is required.")

        if settlement_verified is not False:
            raise ValueError(
                "This application cannot persist a verified-settlement claim."
            )

        timestamp = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO investigation_history (
                    order_id,
                    observed_status,
                    classification,
                    settlement_verified,
                    investigated_at
                )
                VALUES (?, ?, ?, 0, ?)
                """,
                (
                    order_id.strip(),
                    status.strip().upper(),
                    classification.strip(),
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def list_investigations(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest historical snapshots first."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Limit must be an integer.")

        if not 1 <= limit <= 500:
            raise ValueError("Limit must be between 1 and 500.")

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    id,
                    order_id,
                    observed_status,
                    classification,
                    settlement_verified,
                    investigated_at
                FROM investigation_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]
