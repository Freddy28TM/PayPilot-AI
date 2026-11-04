"""Tests for persistent reconciliation run history."""

import sqlite3

import pytest

from backend.database import ReconciliationRepository


@pytest.fixture
def repository(tmp_path):
    repo = ReconciliationRepository(tmp_path / "data" / "reconciliation.sqlite3")
    repo.initialize()
    return repo


def make_result():
    rows = [
        {
            "merchant_reference": "INV-001",
            "paypal_order_id": "ORDER-001",
            "expected_amount": "10.00",
            "expected_currency": "USD",
            "observed_amount": "10.00",
            "observed_currency": "USD",
            "status": "matched",
            "summary": "Amount and currency match.",
            "settlement_verified": False,
        },
        {
            "merchant_reference": "INV-002",
            "paypal_order_id": "ORDER-002",
            "expected_amount": "20.00",
            "expected_currency": "USD",
            "observed_amount": None,
            "observed_currency": None,
            "status": "order_not_found",
            "summary": "The PayPal order was not found.",
            "settlement_verified": False,
        },
    ]
    return {
        "results": rows,
        "count": 2,
        "counts": {
            "matched": 1,
            "amount_mismatch": 0,
            "currency_mismatch": 0,
            "needs_review": 0,
            "order_not_found": 1,
            "lookup_error": 0,
        },
        "settlement_verified": False,
    }



def test_risk_signals_are_saved_and_restored(repository):
    result = make_result()
    result["results"][0]["risk_signals"] = [
        "duplicate_in_file",
        "historical_identifier_match",
    ]
    result["results"][1]["risk_signals"] = []

    run_id = repository.save_run(result)
    saved = repository.get_run(run_id)

    assert saved is not None
    assert saved["results"][0]["risk_signals"] == [
        "duplicate_in_file",
        "historical_identifier_match",
    ]
    assert saved["results"][1]["risk_signals"] == []
    assert "risk_signals_json" not in saved["results"][0]
    assert saved["settlement_verified"] is False
    assert all(row["settlement_verified"] is False for row in saved["results"])


@pytest.mark.parametrize(
    "risk_signals",
    [
        "duplicate_in_file",
        ["unknown_signal"],
        ["duplicate_in_file", "duplicate_in_file"],
        [None],
    ],
)
def test_invalid_risk_signals_are_rejected(repository, risk_signals):
    result = make_result()
    result["results"][0]["risk_signals"] = risk_signals

    with pytest.raises(ValueError, match="risk signals"):
        repository.save_run(result)

    assert repository.list_runs() == []


def test_initialize_migrates_legacy_database_without_losing_history(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"

    # Construct the pre-migration schema and one existing saved record.
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            CREATE TABLE reconciliation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                record_count INTEGER NOT NULL CHECK (record_count >= 0),
                counts_json TEXT NOT NULL,
                settlement_verified INTEGER NOT NULL
                    CHECK (settlement_verified = 0)
            );

            CREATE TABLE reconciliation_run_items (
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
            );

            INSERT INTO reconciliation_runs
                (id, created_at, record_count, counts_json, settlement_verified)
            VALUES
                (1, '2026-10-20T12:00:00+00:00', 1,
                 '{"matched":1}', 0);

            INSERT INTO reconciliation_run_items (
                run_id, row_number, merchant_reference, paypal_order_id,
                expected_amount, expected_currency, observed_amount,
                observed_currency, status, summary, settlement_verified
            ) VALUES (
                1, 1, 'LEGACY-INV', 'LEGACY-ORDER', '10.00', 'USD',
                '10.00', 'USD', 'matched', 'Legacy saved row.', 0
            );
        """)

    repository = ReconciliationRepository(database_path)
    repository.initialize()

    saved = repository.get_run(1)

    assert saved is not None
    assert saved["record_count"] == 1
    assert saved["results"][0]["merchant_reference"] == "LEGACY-INV"
    assert saved["results"][0]["risk_signals"] == []
    assert saved["settlement_verified"] is False

    # Re-running initialization must be safe and preserve the migrated row.
    repository.initialize()
    saved_again = repository.get_run(1)
    assert saved_again is not None
    assert saved_again["results"][0]["risk_signals"] == []


def test_sparse_status_counts_are_normalized_when_saved(tmp_path):
    repo = ReconciliationRepository(tmp_path / "reconciliation.sqlite3")
    repo.initialize()

    result = make_result()
    result["counts"] = {"matched": 1, "order_not_found": 1}

    run_id = repo.save_run(result)
    saved = repo.get_run(run_id)

    assert saved is not None
    assert saved["counts"] == {
        "matched": 1,
        "amount_mismatch": 0,
        "currency_mismatch": 0,
        "needs_review": 0,
        "order_not_found": 1,
        "lookup_error": 0,
    }


def test_save_and_retrieve_run(repository):
    run_id = repository.save_run(make_result())

    saved = repository.get_run(run_id)
    assert saved is not None
    assert saved["run_id"] == run_id
    assert saved["record_count"] == 2
    assert saved["counts"]["matched"] == 1
    assert [row["row_number"] for row in saved["results"]] == [1, 2]
    assert saved["results"][0]["status"] == "matched"
    assert saved["results"][1]["observed_amount"] is None
    assert saved["settlement_verified"] is False
    assert all(row["settlement_verified"] is False for row in saved["results"])


def test_history_survives_repository_recreation(tmp_path):
    database_path = tmp_path / "data" / "history.sqlite3"
    first = ReconciliationRepository(database_path)
    first.initialize()
    run_id = first.save_run(make_result())

    reopened = ReconciliationRepository(database_path)
    reopened.initialize()

    assert reopened.get_run(run_id) is not None
    assert len(reopened.list_runs()) == 1


def test_runs_are_listed_newest_first(repository):
    first_id = repository.save_run(make_result())
    second_id = repository.save_run(make_result())

    runs = repository.list_runs()

    assert [run["run_id"] for run in runs] == [second_id, first_id]


def test_missing_run_returns_none(repository):
    assert repository.get_run(999) is None


@pytest.mark.parametrize(
    ("change", "exception"),
    [
        (lambda data: data.update(count=True), ValueError),
        (lambda data: data.update(count=1), ValueError),
        (lambda data: data.update(settlement_verified=True), ValueError),
        (lambda data: data.update(counts={}), ValueError),
        (
            lambda data: data["counts"].update(unexpected_status=0),
            ValueError,
        ),
        (
            lambda data: data["results"][0].update(status=[]),
            ValueError,
        ),
        (
            lambda data: data["results"][0].update(settlement_verified=True),
            ValueError,
        ),
        (
            lambda data: data["results"][0].update(observed_amount=""),
            ValueError,
        ),
        (
            lambda data: data["results"][0].update(observed_currency=""),
            ValueError,
        ),
    ],
)
def test_invalid_runs_are_rejected_without_saving(repository, change, exception):
    result = make_result()
    change(result)

    with pytest.raises(exception):
        repository.save_run(result)

    assert repository.list_runs() == []


def test_failed_row_insert_rolls_back_the_whole_run(repository):
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_test_row
            BEFORE INSERT ON reconciliation_run_items
            BEGIN
                SELECT RAISE(ABORT, 'simulated row insert failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated row insert failure"):
        repository.save_run(make_result())

    assert repository.list_runs() == []



def test_find_previous_matches_returns_identifier_and_run_details(repository):
    earlier_run_id = repository.save_run(make_result())

    matches = repository.find_previous_matches(
        merchant_reference=" INV-001 ",
        paypal_order_id=" ORDER-001 ",
    )

    assert matches == [
        {
            "match_type": "merchant_reference",
            "run_id": earlier_run_id,
            "row_number": 1,
            "matched_value": "INV-001",
        },
        {
            "match_type": "paypal_order_id",
            "run_id": earlier_run_id,
            "row_number": 1,
            "matched_value": "ORDER-001",
        },
    ]



def test_find_previous_matches_reports_conflicting_identifiers(repository):
    merchant_run = make_result()
    merchant_run["results"][0]["merchant_reference"] = "INV-CONFLICT"
    merchant_run["results"][0]["paypal_order_id"] = "ORDER-A"
    merchant_run_id = repository.save_run(merchant_run)

    order_run = make_result()
    order_run["results"][0]["merchant_reference"] = "INV-B"
    order_run["results"][0]["paypal_order_id"] = "ORDER-CONFLICT"
    order_run_id = repository.save_run(order_run)

    matches = repository.find_previous_matches(
        merchant_reference="INV-CONFLICT",
        paypal_order_id="ORDER-CONFLICT",
    )

    assert matches == [
        {
            "match_type": "paypal_order_id",
            "run_id": order_run_id,
            "row_number": 1,
            "matched_value": "ORDER-CONFLICT",
        },
        {
            "match_type": "merchant_reference",
            "run_id": merchant_run_id,
            "row_number": 1,
            "matched_value": "INV-CONFLICT",
        },
    ]

def test_find_previous_matches_returns_empty_list_when_unseen(repository):
    repository.save_run(make_result())

    matches = repository.find_previous_matches(
        merchant_reference="NEW-REF",
        paypal_order_id="NEW-ORDER",
    )

    assert matches == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("merchant_reference", ""),
        ("merchant_reference", "  "),
        ("paypal_order_id", ""),
        ("paypal_order_id", "  "),
        ("merchant_reference", None),
        ("paypal_order_id", 123),
    ],
)
def test_find_previous_matches_rejects_invalid_identifiers(
    repository, field, value
):
    arguments = {
        "merchant_reference": "INV-001",
        "paypal_order_id": "ORDER-001",
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        repository.find_previous_matches(**arguments)

    assert repository.list_runs() == []

def test_empty_run_can_be_saved(repository):
    result = make_result()
    result["results"] = []
    result["count"] = 0
    result["counts"] = {status: 0 for status in result["counts"]}

    run_id = repository.save_run(result)
    saved = repository.get_run(run_id)

    assert saved is not None
    assert saved["record_count"] == 0
    assert saved["results"] == []
