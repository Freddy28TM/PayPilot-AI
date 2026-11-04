"""Read-only analytics over persisted reconciliation history."""

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

RECONCILIATION_STATUSES = (
    "matched",
    "amount_mismatch",
    "currency_mismatch",
    "needs_review",
    "order_not_found",
    "lookup_error",
)


class ReconciliationAnalytics:
    """Calculate metrics from saved reconciliation runs and rows."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def _connect_read_only(self) -> sqlite3.Connection:
        """Open the existing database without permitting writes."""
        database_uri = (
            f"file:{quote(str(self.database_path.resolve()))}?mode=ro"
        )
        connection = sqlite3.connect(database_uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def summary(self) -> dict:
        """Return totals across all saved runs, not just recent history."""
        with closing(self._connect_read_only()) as connection:
            run = connection.execute(
                """
                SELECT COUNT(*) AS total_runs,
                       COALESCE(SUM(record_count), 0) AS recorded_rows,
                       MAX(created_at) AS latest_run_at
                FROM reconciliation_runs
                """
            ).fetchone()

            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS row_count
                FROM reconciliation_run_items
                GROUP BY status
                """
            ).fetchall()

            risk_signal_history_available = connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM reconciliation_run_items
                    WHERE risk_signals_json IS NOT NULL
                      AND risk_signals_json != '[]'
                )
                """
            ).fetchone()[0] == 1

        status_counts = {status: 0 for status in RECONCILIATION_STATUSES}
        for row in rows:
            status_counts[row["status"]] = row["row_count"]

        # Count every stored row, including any unexpected status.
        total_rows = sum(row["row_count"] for row in rows)
        matched = status_counts["matched"]

        return {
            "total_runs": run["total_runs"],
            "total_rows": total_rows,
            "recorded_rows": run["recorded_rows"],
            "record_count_consistent": (
                run["recorded_rows"] == total_rows
            ),
            "status_counts": status_counts,
            "matched_rows": matched,
            "exception_rows": total_rows - matched,
            "match_rate_percent": (
                round(matched * 100 / total_rows, 2) if total_rows else 0.0
            ),
            "latest_run_at": run["latest_run_at"],
            "settlement_verified": False,
            "risk_signal_history_available": (
                risk_signal_history_available
            ),
        }

    def trends(self, limit: int = 30) -> dict:
        """Return daily aggregates for the most recent days with saved runs."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Limit must be an integer.")
        if not 1 <= limit <= 365:
            raise ValueError("Limit must be between 1 and 365.")

        with closing(self._connect_read_only()) as connection:
            rows = connection.execute(
                """
                SELECT
                    substr(r.created_at, 1, 10) AS day,
                    COUNT(DISTINCT r.id) AS total_runs,
                    COUNT(i.id) AS total_rows,
                    SUM(CASE WHEN i.status = 'matched'
                             THEN 1 ELSE 0 END) AS matched_rows,
                    SUM(CASE WHEN i.id IS NOT NULL
                                  AND i.status != 'matched'
                             THEN 1 ELSE 0 END) AS exception_rows,
                    SUM(CASE WHEN i.status = 'needs_review'
                             THEN 1 ELSE 0 END) AS needs_review_rows,
                    SUM(CASE WHEN i.status = 'amount_mismatch'
                             THEN 1 ELSE 0 END) AS amount_mismatch_rows,
                    SUM(CASE WHEN i.status = 'currency_mismatch'
                             THEN 1 ELSE 0 END) AS currency_mismatch_rows,
                    SUM(CASE WHEN i.status IN (
                                'order_not_found', 'lookup_error')
                             THEN 1 ELSE 0 END) AS lookup_failure_rows
                FROM reconciliation_runs AS r
                LEFT JOIN reconciliation_run_items AS i ON i.run_id = r.id
                GROUP BY substr(r.created_at, 1, 10)
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        days = []
        for row in reversed(rows):
            item = dict(row)
            for key, value in item.items():
                if key.endswith("_rows") or key == "total_runs":
                    item[key] = value or 0
            days.append(item)

        return {
            "days": days,
            "count": len(days),
            "limit": limit,
            "settlement_verified": False,
        }

    def investigation_summary(self) -> dict:
        """Return totals over saved PayPal investigations.

        Reads investigation_history only. Never claims settlement: the
        schema enforces settlement_verified = 0, and this method echoes
        that as False regardless of classification strings.
        """
        with closing(self._connect_read_only()) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM investigation_history"
            ).fetchone()[0]

            rows = connection.execute(
                """
                SELECT classification, COUNT(*) AS row_count
                FROM investigation_history
                GROUP BY classification
                """
            ).fetchall()

        classification_counts: dict[str, int] = {}
        for row in rows:
            classification_counts[row["classification"]] = row["row_count"]

        return {
            "total_investigations": total,
            "classification_counts": classification_counts,
            "settlement_verified": False,
        }
