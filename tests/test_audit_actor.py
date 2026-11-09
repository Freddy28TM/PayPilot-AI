"""Audit actor attribution and its database migration."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthRepository
from backend.main import app, get_auth_repository
from tests.test_account_management import (
    EMPLOYEE_PASSWORD,
    PASSWORD,
    headers,
    make_employee,
    sign_in_employee,
)

OLD_AUDIT_TABLE = """
    CREATE TABLE auth_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES auth_users(id),
        username TEXT NOT NULL,
        event_type TEXT NOT NULL,
        succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
        occurred_at TEXT NOT NULL
    )
"""


def _columns(path):
    with sqlite3.connect(path) as connection:
        return [
            row[1]
            for row in connection.execute("PRAGMA table_info(auth_audit_events)")
        ]


def test_fresh_database_has_actor_column(tmp_path):
    database = tmp_path / "fresh.sqlite3"
    AuthRepository(database).initialize()
    assert "actor_user_id" in _columns(database)


def test_migration_adds_column_and_preserves_existing_events(tmp_path):
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE auth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('owner', 'employee')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by INTEGER REFERENCES auth_users(id)
            )
        """)
        connection.execute(OLD_AUDIT_TABLE)
        connection.execute(
            "INSERT INTO auth_audit_events "
            "(user_id, username, event_type, succeeded, occurred_at) "
            "VALUES (NULL, 'legacy', 'login', 1, '2026-01-01T00:00:00Z')"
        )
    assert "actor_user_id" not in _columns(database)

    repository = AuthRepository(database)
    repository.initialize()
    repository.initialize()  # idempotent: a second run must not fail

    assert "actor_user_id" in _columns(database)
    events = repository.list_audit_events()
    assert len(events) == 1
    assert events[0]["subject"] == "legacy"
    assert events[0]["actor"] is None


def test_create_and_deactivate_record_the_acting_owner(tmp_path):
    repository = AuthRepository(tmp_path / "a.sqlite3")
    repository.initialize()
    owner_id = repository.create_initial_owner("the.owner", PASSWORD)
    employee_id = repository.create_user(
        "emp.one", EMPLOYEE_PASSWORD, "employee", created_by=owner_id
    )
    repository.set_user_active(employee_id, False, owner_id)
    repository.set_user_active(employee_id, True, owner_id)

    events = repository.list_audit_events()
    by_type = {e["event_type"]: e for e in events}
    assert by_type["account_deactivated"]["actor"] == "the.owner"
    assert by_type["account_reactivated"]["actor"] == "the.owner"
    created = [e for e in events if e["event_type"] == "account_created"]
    actors = {e["subject"]: e["actor"] for e in created}
    assert actors == {"the.owner": None, "emp.one": "the.owner"}


def test_audit_limit_is_validated_and_clamped(tmp_path):
    repository = AuthRepository(tmp_path / "a.sqlite3")
    repository.initialize()
    owner_id = repository.create_initial_owner("the.owner", PASSWORD)
    for index in range(3):
        repository.create_user(
            f"emp.{index}", EMPLOYEE_PASSWORD, "employee", created_by=owner_id
        )
    assert len(repository.list_audit_events(2)) == 2
    assert len(repository.list_audit_events(0)) == 1  # clamped up to 1
    with pytest.raises(TypeError):
        repository.list_audit_events("5")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        repository.list_audit_events(True)  # type: ignore[arg-type]


@pytest.mark.real_auth
class TestAuditEndpoint:
    @pytest.fixture
    def clients(self, tmp_path):
        repository = AuthRepository(tmp_path / "auth.sqlite3")
        repository.initialize()
        app.dependency_overrides[get_auth_repository] = lambda: repository
        try:
            with (
                TestClient(app, follow_redirects=False) as owner,
                TestClient(app, follow_redirects=False) as employee,
                TestClient(app, follow_redirects=False) as anonymous,
            ):
                owner.post(
                    "/api/auth/setup",
                    json={"username": "the.owner", "password": PASSWORD},
                ).raise_for_status()
                yield owner, employee, anonymous
        finally:
            app.dependency_overrides.clear()

    def test_owner_sees_actor_for_account_changes(self, clients):
        owner, _, _ = clients
        created = make_employee(owner)
        owner.post(
            f"/api/auth/users/{created['id']}/deactivate", headers=headers(owner)
        ).raise_for_status()

        body = owner.get("/api/auth/audit").json()
        by_type = {e["event_type"]: e for e in body["events"]}
        assert body["count"] == len(body["events"])
        assert by_type["account_deactivated"]["actor"] == "the.owner"
        assert by_type["account_deactivated"]["subject"] == "emp.one"
        assert "password" not in str(body).lower()

    def test_employee_cannot_read_audit(self, clients):
        owner, employee, _ = clients
        make_employee(owner)
        sign_in_employee(employee)
        assert employee.get("/api/auth/audit").status_code == 403

    def test_anonymous_cannot_read_audit(self, clients):
        _, _, anonymous = clients
        assert anonymous.get("/api/auth/audit").status_code == 401

    @pytest.mark.parametrize("limit", ["0", "501", "abc", "-1"])
    def test_invalid_limit_is_rejected(self, clients, limit):
        owner, _, _ = clients
        assert owner.get(f"/api/auth/audit?limit={limit}").status_code == 422
