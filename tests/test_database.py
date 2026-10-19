"""Tests for PayPilot's local investigation history repository."""

import sqlite3

import pytest

from backend.database import InvestigationRepository


@pytest.fixture
def repository(tmp_path):
    repo = InvestigationRepository(tmp_path / "data" / "test.sqlite3")
    repo.initialize()
    return repo


@pytest.fixture
def evidence():
    return {"id": "ORDER-1", "status": "COMPLETED"}


@pytest.fixture
def assessment():
    return {
        "classification": "completed_settlement_unverified",
        "settlement_verified": False,
    }


def test_initialize_creates_database_and_history_table(repository):
    assert repository.database_path.exists()

    with sqlite3.connect(repository.database_path) as connection:
        result = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'investigation_history'
            """
        ).fetchone()

    assert result is not None


def test_initialize_is_safe_to_repeat(repository):
    repository.initialize()
    assert repository.list_investigations() == []


def test_save_and_retrieve_investigation(repository, evidence, assessment):
    record_id = repository.save_investigation(evidence, assessment)

    records = repository.list_investigations()

    assert record_id == 1
    assert len(records) == 1
    assert records[0]["order_id"] == "ORDER-1"
    assert records[0]["observed_status"] == "COMPLETED"
    assert records[0]["classification"] == (
        "completed_settlement_unverified"
    )
    assert records[0]["settlement_verified"] == 0
    assert records[0]["investigated_at"].endswith("+00:00")


def test_repeated_investigations_create_separate_history_records(
    repository, evidence, assessment
):
    first_id = repository.save_investigation(evidence, assessment)

    updated_evidence = {"id": "ORDER-1", "status": "APPROVED"}
    updated_assessment = {
        "classification": "order_approved_not_completed",
        "settlement_verified": False,
    }
    second_id = repository.save_investigation(
        updated_evidence, updated_assessment
    )

    records = repository.list_investigations()

    assert second_id > first_id
    assert len(records) == 2
    assert records[0]["observed_status"] == "APPROVED"
    assert records[1]["observed_status"] == "COMPLETED"


@pytest.mark.parametrize(
    ("bad_evidence", "bad_assessment"),
    [
        ({}, {"classification": "insufficient_evidence", "settlement_verified": False}),
        (
            {"id": " ", "status": "COMPLETED"},
            {"classification": "insufficient_evidence", "settlement_verified": False},
        ),
        (
            {"id": "ORDER-1", "status": ""},
            {"classification": "insufficient_evidence", "settlement_verified": False},
        ),
        (
            {"id": "ORDER-1", "status": "COMPLETED"},
            {"classification": "", "settlement_verified": False},
        ),
        (
            {"id": "ORDER-1", "status": "COMPLETED"},
            {"classification": "insufficient_evidence", "settlement_verified": True},
        ),
        (
            {"id": "ORDER-1", "status": "COMPLETED"},
            {"classification": "insufficient_evidence", "settlement_verified": 0},
        ),
    ],
)
def test_invalid_records_are_rejected_without_saving(
    repository, bad_evidence, bad_assessment
):
    with pytest.raises(ValueError):
        repository.save_investigation(bad_evidence, bad_assessment)

    assert repository.list_investigations() == []


@pytest.mark.parametrize("limit", [True, 1.5, "10"])
def test_history_limit_rejects_invalid_types(repository, limit):
    with pytest.raises(TypeError, match="Limit must be an integer"):
        repository.list_investigations(limit)


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_history_limit_rejects_out_of_range_values(repository, limit):
    with pytest.raises(
        ValueError, match="Limit must be between 1 and 500"
    ):
        repository.list_investigations(limit)


def test_history_limit_returns_newest_records(repository, assessment):
    for number in range(3):
        repository.save_investigation(
            {"id": f"ORDER-{number}", "status": "CREATED"},
            assessment,
        )

    records = repository.list_investigations(limit=2)

    assert [record["order_id"] for record in records] == [
        "ORDER-2",
        "ORDER-1",
    ]


def test_payer_data_and_raw_response_fields_are_not_persisted(
    repository, assessment
):
    evidence_with_extra_data = {
        "id": "ORDER-PRIVATE",
        "status": "COMPLETED",
        "payer": {"email_address": "synthetic@example.test"},
        "internal_test_field": "must-not-be-stored",
    }

    repository.save_investigation(evidence_with_extra_data, assessment)
    records = repository.list_investigations()

    assert len(records) == 1
    assert set(records[0]) == {
        "id",
        "order_id",
        "observed_status",
        "classification",
        "settlement_verified",
        "investigated_at",
    }
    assert "synthetic@example.test" not in repr(records)
    assert "must-not-be-stored" not in repr(records)
