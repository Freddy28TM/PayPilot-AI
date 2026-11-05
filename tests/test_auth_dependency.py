"""Tests for the authentication repository application dependency."""

import sqlite3

import pytest
from fastapi import HTTPException

from backend import main
from backend.auth import AuthRepository


def test_auth_repository_dependency_initializes_isolated_database(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "auth-test.sqlite3"
    repository = AuthRepository(database_path)

    monkeypatch.setattr(
        main,
        "AuthRepository",
        lambda: repository,
    )

    result = main.get_auth_repository()

    assert result is repository
    assert database_path.exists()

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "auth_users",
        "auth_sessions",
        "auth_audit_events",
    }.issubset(tables)


def test_auth_repository_dependency_maps_database_failure(monkeypatch):
    class BrokenAuthRepository:
        def initialize(self):
            raise sqlite3.OperationalError("controlled test failure")

    monkeypatch.setattr(
        main,
        "AuthRepository",
        BrokenAuthRepository,
    )

    with pytest.raises(HTTPException) as error:
        main.get_auth_repository()

    assert error.value.status_code == 503
    assert error.value.detail == (
        "Authentication services are temporarily unavailable."
    )
