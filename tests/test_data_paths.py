"""Tests for runtime data location resolution."""

import os
import stat

import pytest

from backend import paths
from backend.auth import AuthRepository
from backend.database import InvestigationRepository, ReconciliationRepository


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch, tmp_path):
    # Run from an empty directory so a developer's real .env is never read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)


def test_default_is_legacy_in_repo_layout():
    result = paths.get_data_paths()
    assert result.legacy_layout is True
    assert result.database == paths.LEGACY_DATA_DIR / "paypilot.sqlite3"


def test_env_override_uses_databases_subdirectory(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "pp"))
    result = paths.get_data_paths()
    assert result.legacy_layout is False
    assert result.database == tmp_path / "pp" / "databases" / "paypilot.sqlite3"
    assert result.backups == tmp_path / "pp" / "backups"
    assert result.recovery == tmp_path / "pp" / "recovery"


def test_relative_data_dir_is_rejected(monkeypatch):
    monkeypatch.setenv(paths.DATA_DIR_ENV, "relative/dir")
    with pytest.raises(ValueError, match="absolute"):
        paths.get_data_paths()


def test_blank_data_dir_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv(paths.DATA_DIR_ENV, "   ")
    assert paths.get_data_paths().legacy_layout is True


def test_dotenv_file_is_honoured(tmp_path):
    (tmp_path / ".env").write_text(f"PAYPILOT_DATA_DIR={tmp_path}/fromenv\n")
    result = paths.get_data_paths()
    assert result.root == tmp_path / "fromenv"


def test_ensure_directories_creates_private_tree(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "pp"))
    result = paths.get_data_paths()
    result.ensure_directories()
    for directory in (
        result.databases,
        result.backups,
        result.logs,
        result.exports,
        result.recovery,
    ):
        assert directory.is_dir()
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700


def test_repositories_default_to_configured_location(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "pp"))
    expected = tmp_path / "pp" / "databases" / "paypilot.sqlite3"
    assert InvestigationRepository().database_path == expected
    assert ReconciliationRepository().database_path == expected
    assert AuthRepository().database_path == expected


def test_explicit_path_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "pp"))
    explicit = tmp_path / "explicit.sqlite3"
    assert InvestigationRepository(explicit).database_path == explicit
