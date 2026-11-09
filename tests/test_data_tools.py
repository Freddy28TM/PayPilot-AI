"""Tests for backup and restore safety."""

import sqlite3
import stat

import pytest

from backend import paths
from backend.data_tools import DataToolError, create_backup, restore_backup


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "pp"))
    paths.get_data_paths().ensure_directories()
    database = paths.get_data_paths().database
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (v TEXT)")
        connection.execute("INSERT INTO t VALUES ('keep-me')")
    return database


def _rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT v FROM t").fetchall()


def test_backup_is_verified_private_and_complete(live):
    backup = create_backup()
    assert backup.parent == paths.get_data_paths().backups
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert _rows(backup) == [("keep-me",)]


def test_backup_of_missing_database_fails(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "empty"))
    with pytest.raises(DataToolError, match="not found"):
        create_backup()


def test_restore_writes_new_verified_copy_in_recovery(live):
    backup = create_backup()
    restored = restore_backup(backup)
    assert restored.parent == paths.get_data_paths().recovery
    assert _rows(restored) == [("keep-me",)]


def test_restore_refuses_live_database(live):
    backup = create_backup()
    with pytest.raises(DataToolError, match="live database"):
        restore_backup(backup, target=live)


def test_restore_refuses_to_overwrite_existing_file(live, tmp_path):
    backup = create_backup()
    existing = tmp_path / "existing.sqlite3"
    existing.write_text("precious")
    with pytest.raises(DataToolError, match="overwrite"):
        restore_backup(backup, target=existing)
    assert existing.read_text() == "precious"


def test_restore_rejects_corrupt_backup(live, tmp_path):
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"this is not a database" * 100)
    with pytest.raises(DataToolError):
        restore_backup(bad)
    assert not list(paths.get_data_paths().recovery.glob("restored-*"))


def test_restore_rejects_missing_backup(live, tmp_path):
    with pytest.raises(DataToolError, match="Not a file"):
        restore_backup(tmp_path / "nope.sqlite3")
