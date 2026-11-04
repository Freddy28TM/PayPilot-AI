"""Tests for read-only reconciliation analytics."""

import sqlite3

import pytest

from backend.analytics import ReconciliationAnalytics
from backend.database import ReconciliationRepository


def make_run(statuses):
    rows = []
    counts = {}
    for index, status in enumerate(statuses, start=1):
        rows.append({
            "merchant_reference": f"INV-{index}",
            "paypal_order_id": f"ORDER-{index}",
            "expected_amount": "10.00",
            "expected_currency": "USD",
            "observed_amount": "10.00" if status == "matched" else None,
            "observed_currency": "USD" if status == "matched" else None,
            "status": status,
            "summary": "Analytics test fixture.",
            "settlement_verified": False,
        })
        counts[status] = counts.get(status, 0) + 1

    return {
        "results": rows,
        "count": len(rows),
        "counts": counts,
        "settlement_verified": False,
    }


@pytest.fixture
def setup_analytics(tmp_path):
    database_path = tmp_path / "analytics.sqlite3"
    repository = ReconciliationRepository(database_path)
    repository.initialize()
    analytics = ReconciliationAnalytics(database_path)
    return repository, analytics, database_path


def test_summary_counts_saved_rows_and_exceptions(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run([
        "matched",
        "matched",
        "needs_review",
        "amount_mismatch",
        "order_not_found",
        "lookup_error",
        "currency_mismatch",
    ]))

    result = analytics.summary()

    assert result["total_runs"] == 1
    assert result["total_rows"] == 7
    assert result["recorded_rows"] == 7
    assert result["matched_rows"] == 2
    assert result["exception_rows"] == 5
    assert result["match_rate_percent"] == 28.57
    assert result["status_counts"] == {
        "matched": 2,
        "amount_mismatch": 1,
        "currency_mismatch": 1,
        "needs_review": 1,
        "order_not_found": 1,
        "lookup_error": 1,
    }
    assert result["settlement_verified"] is False
    assert result["risk_signal_history_available"] is False


def test_summary_reports_persisted_risk_signal_history(setup_analytics):
    repository, analytics, _ = setup_analytics
    run = make_run(["needs_review"])
    run["results"][0]["risk_signals"] = ["duplicate_in_file"]
    repository.save_run(run)

    result = analytics.summary()

    assert result["risk_signal_history_available"] is True
    assert result["settlement_verified"] is False


def test_empty_risk_signals_do_not_enable_history_flag(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run(["needs_review"]))

    result = analytics.summary()

    assert result["risk_signal_history_available"] is False
    assert result["settlement_verified"] is False


def test_empty_history_returns_zero_totals(setup_analytics):
    _, analytics, _ = setup_analytics

    result = analytics.summary()

    assert result["total_runs"] == 0
    assert result["total_rows"] == 0
    assert result["exception_rows"] == 0
    assert result["match_rate_percent"] == 0.0
    assert result["latest_run_at"] is None
    assert result["settlement_verified"] is False


def test_summary_aggregates_multiple_runs(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run(["matched", "needs_review"]))
    repository.save_run(make_run(["matched", "matched"]))

    result = analytics.summary()

    assert result["total_runs"] == 2
    assert result["total_rows"] == 4
    assert result["matched_rows"] == 3
    assert result["exception_rows"] == 1
    assert result["match_rate_percent"] == 75.0


def test_trends_include_saved_runs_and_status_breakdown(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run([
        "matched",
        "needs_review",
        "amount_mismatch",
        "order_not_found",
    ]))

    result = analytics.trends()

    assert result["count"] == 1
    day = result["days"][0]
    assert day["total_runs"] == 1
    assert day["total_rows"] == 4
    assert day["matched_rows"] == 1
    assert day["exception_rows"] == 3
    assert day["needs_review_rows"] == 1
    assert day["amount_mismatch_rows"] == 1
    assert day["lookup_failure_rows"] == 1
    assert result["settlement_verified"] is False


@pytest.mark.parametrize("limit", [0, -1, 366, True, "30"])
def test_trends_reject_invalid_limits(setup_analytics, limit):
    _, analytics, _ = setup_analytics

    with pytest.raises((TypeError, ValueError)):
        analytics.trends(limit)


def test_analytics_database_connection_rejects_writes(setup_analytics):
    _, analytics, database_path = setup_analytics

    with (
        analytics._connect_read_only() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute(
                "INSERT INTO reconciliation_runs "
                "(created_at, record_count, counts_json, settlement_verified) "
                "VALUES ('test', 0, '{}', 0)"
            )

    assert database_path.exists()

def test_empty_saved_run_does_not_create_a_trend_exception(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run([]))

    result = analytics.trends()

    assert result["count"] == 1
    assert result["days"][0]["total_runs"] == 1
    assert result["days"][0]["total_rows"] == 0
    assert result["days"][0]["exception_rows"] == 0


def test_summary_reports_recorded_row_consistency(setup_analytics):
    repository, analytics, _ = setup_analytics
    repository.save_run(make_run(["matched", "needs_review"]))

    result = analytics.summary()

    assert result["recorded_rows"] == 2
    assert result["total_rows"] == 2
    assert result["record_count_consistent"] is True


def test_summary_closes_read_only_database_connection(setup_analytics):
    _, analytics, _ = setup_analytics
    connections = []

    original_connect = analytics._connect_read_only

    def tracked_connect():
        connection = original_connect()
        connections.append(connection)
        return connection

    analytics._connect_read_only = tracked_connect

    analytics.summary()

    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_investigation_summary_counts_saved_investigations(setup_analytics):
    """Analytics must be able to report saved PayPal investigations.

    investigation_history is a separate operational record from
    reconciliation runs. The handover requires that these not be
    conflated; this test pins that analytics can count investigations
    at all, without implying settlement was verified.
    """
    from backend.database import InvestigationRepository

    _, analytics, database_path = setup_analytics
    inv_repo = InvestigationRepository(database_path)
    inv_repo.initialize()

    inv_repo.save_investigation(
        {"id": "ORDER-A", "status": "COMPLETED"},
        {
            "classification": "completed_settlement_unverified",
            "settlement_verified": False,
        },
    )
    inv_repo.save_investigation(
        {"id": "ORDER-B", "status": "APPROVED"},
        {
            "classification": "order_approved_not_completed",
            "settlement_verified": False,
        },
    )
    inv_repo.save_investigation(
        {"id": "ORDER-A", "status": "COMPLETED"},
        {
            "classification": "completed_settlement_unverified",
            "settlement_verified": False,
        },
    )

    result = analytics.investigation_summary()

    assert result["total_investigations"] == 3
    assert result["classification_counts"] == {
        "completed_settlement_unverified": 2,
        "order_approved_not_completed": 1,
    }
    assert result["settlement_verified"] is False
