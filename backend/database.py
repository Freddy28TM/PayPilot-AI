"""Local persistence for minimal PayPilot investigation history."""

import json
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


ALLOWED_RECONCILIATION_STATUSES = frozenset({
    "matched",
    "amount_mismatch",
    "currency_mismatch",
    "needs_review",
    "order_not_found",
    "lookup_error",
})


class ReconciliationRepository:
    """Persist minimal, immutable snapshots of reconciliation runs."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        """Create reconciliation history tables when needed."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reconciliation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL CHECK (record_count >= 0),
                    counts_json TEXT NOT NULL,
                    settlement_verified INTEGER NOT NULL
                        CHECK (settlement_verified = 0)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reconciliation_run_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL
                        REFERENCES reconciliation_runs(id) ON DELETE CASCADE,
                    row_number INTEGER NOT NULL CHECK (row_number > 0),
                    merchant_reference TEXT NOT NULL,
                    paypal_order_id TEXT NOT NULL,
                    expected_amount TEXT NOT NULL,
                    expected_currency TEXT NOT NULL,
                    observed_amount TEXT,
                    observed_currency TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'matched', 'amount_mismatch', 'currency_mismatch',
                            'needs_review', 'order_not_found', 'lookup_error'
                        )
                    ),
                    summary TEXT NOT NULL,
                    settlement_verified INTEGER NOT NULL
                        CHECK (settlement_verified = 0),
                    UNIQUE (run_id, row_number)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_created
                ON reconciliation_runs(id DESC)
            """)

    @staticmethod
    def _required_text(value: Any, field: str, max_length: int) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > max_length
        ):
            raise ValueError(f"Invalid reconciliation field: {field}.")
        return value.strip()

    def save_run(self, result: dict[str, Any]) -> int:
        """Validate and atomically persist a complete reconciliation snapshot."""
        if not isinstance(result, dict):
            raise TypeError("A reconciliation result object is required.")

        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("Reconciliation results must be a list of at most 1000 rows.")

        if (
            isinstance(result.get("count"), bool)
            or not isinstance(result.get("count"), int)
            or result.get("count") != len(rows)
        ):
            raise ValueError("Reconciliation result count does not match its rows.")

        if result.get("settlement_verified") is not False:
            raise ValueError("Verified settlement cannot be recorded.")

        status_counts = {status: 0 for status in ALLOWED_RECONCILIATION_STATUSES}
        clean_rows = []

        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise TypeError("Every reconciliation row must be an object.")

            if row.get("settlement_verified") is not False:
                raise ValueError("Verified settlement cannot be recorded.")

            status = row.get("status")
            if (
                not isinstance(status, str)
                or status not in ALLOWED_RECONCILIATION_STATUSES
            ):
                raise ValueError("An unsupported reconciliation status was supplied.")

            expected_amount = self._required_text(
                row.get("expected_amount"), "expected_amount", 32
            )
            expected_currency = self._required_text(
                row.get("expected_currency"), "expected_currency", 3
            )
            observed_amount = row.get("observed_amount")
            observed_currency = row.get("observed_currency")

            for field, value, limit in (
                ("observed_amount", observed_amount, 32),
                ("observed_currency", observed_currency, 3),
            ):
                if value is not None and (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > limit
                ):
                    raise ValueError(f"Invalid reconciliation field: {field}.")

            clean_rows.append((
                row_number,
                self._required_text(
                    row.get("merchant_reference"), "merchant_reference", 200
                ),
                self._required_text(
                    row.get("paypal_order_id"), "paypal_order_id", 128
                ),
                expected_amount,
                expected_currency,
                observed_amount,
                observed_currency,
                status,
                self._required_text(row.get("summary"), "summary", 1000),
            ))
            status_counts[status] += 1

        supplied_counts = result.get("counts")
        if not isinstance(supplied_counts, dict):
            raise TypeError("Reconciliation status counts are required.")

        if not set(supplied_counts).issubset(ALLOWED_RECONCILIATION_STATUSES):
            raise ValueError("Reconciliation status counts have invalid keys.")

        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in supplied_counts.values()
        ):
            raise ValueError("Reconciliation status counts are invalid.")

        normalized_counts = {
            status: 0 for status in ALLOWED_RECONCILIATION_STATUSES
        }
        normalized_counts.update(supplied_counts)
        if normalized_counts != status_counts:
            raise ValueError("Reconciliation status counts do not match the rows.")

        created_at = datetime.now(UTC).isoformat()

        # One transaction ensures a run is never saved without all its rows.
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute(
                """
                INSERT INTO reconciliation_runs (
                    created_at, record_count, counts_json, settlement_verified
                ) VALUES (?, ?, ?, 0)
                """,
                (
                    created_at,
                    len(clean_rows),
                    json.dumps(status_counts, sort_keys=True),
                ),
            )
            run_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO reconciliation_run_items (
                    run_id, row_number, merchant_reference, paypal_order_id,
                    expected_amount, expected_currency, observed_amount,
                    observed_currency, status, summary, settlement_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                [(run_id, *row) for row in clean_rows],
            )

        return run_id

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent reconciliation run summaries, newest first."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Limit must be an integer.")
        if not 1 <= limit <= 500:
            raise ValueError("Limit must be between 1 and 500.")

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, created_at, record_count, counts_json,
                       settlement_verified
                FROM reconciliation_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "run_id": row["id"],
                "created_at": row["created_at"],
                "record_count": row["record_count"],
                "counts": json.loads(row["counts_json"]),
                "settlement_verified": False,
            }
            for row in rows
        ]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        """Return one saved run and its ordered result rows."""
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            raise ValueError("Run ID must be a positive integer.")

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            run = connection.execute(
                """
                SELECT id, created_at, record_count, counts_json
                FROM reconciliation_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()

            if run is None:
                return None

            rows = connection.execute(
                """
                SELECT row_number, merchant_reference, paypal_order_id,
                       expected_amount, expected_currency, observed_amount,
                       observed_currency, status, summary
                FROM reconciliation_run_items
                WHERE run_id = ?
                ORDER BY row_number
                """,
                (run_id,),
            ).fetchall()

        return {
            "run_id": run["id"],
            "created_at": run["created_at"],
            "record_count": run["record_count"],
            "counts": json.loads(run["counts_json"]),
            "settlement_verified": False,
            "results": [
                {**dict(row), "settlement_verified": False}
                for row in rows
            ],
        }
