"""Safe SQLite backup and restore helpers.

Backups use SQLite's online backup API, so they are consistent even while the
application is running. Restores never overwrite an existing file: they write
a verified copy to a new path (by default in the recovery directory) so an
operator can inspect it before putting it into service.
"""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.paths import get_data_paths


class DataToolError(Exception):
    """A backup or restore could not be completed safely."""


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def verify_database(path: Path) -> None:
    """Raise DataToolError unless ``path`` is an intact SQLite database."""
    if not path.is_file():
        raise DataToolError(f"Not a file: {path}")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DataToolError(f"Not a readable SQLite database: {path}") from exc
    if not result or result[0] != "ok":
        raise DataToolError(f"Integrity check failed for {path}")


def _copy_database(source: Path, destination: Path) -> None:
    """Copy via the SQLite backup API into a brand-new file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL guarantees we never overwrite an existing file.
    try:
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError as exc:
        raise DataToolError(f"Refusing to overwrite: {destination}") from exc
    os.close(descriptor)
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        verify_database(destination)
    except (sqlite3.Error, DataToolError) as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, DataToolError):
            raise
        raise DataToolError(f"Copy failed: {exc}") from exc
    os.chmod(destination, 0o600)


def create_backup(
    source: Path | None = None, backup_dir: Path | None = None
) -> Path:
    """Write a verified, timestamped backup and return its path."""
    paths = get_data_paths()
    source = Path(source) if source else paths.database
    backup_dir = Path(backup_dir) if backup_dir else paths.backups
    if not source.is_file():
        raise DataToolError(f"Database not found: {source}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    destination = backup_dir / f"paypilot-{_timestamp()}.sqlite3"
    _copy_database(source, destination)
    return destination


def restore_backup(
    backup: Path,
    target: Path | None = None,
    live_database: Path | None = None,
) -> Path:
    """Restore ``backup`` to a NEW file and return its path.

    Never overwrites an existing file and never writes to the live database.
    """
    paths = get_data_paths()
    backup = Path(backup)
    live = Path(live_database) if live_database else paths.database
    if target is None:
        target = paths.recovery / f"restored-{_timestamp()}.sqlite3"
    target = Path(target)

    if target.resolve() == live.resolve():
        raise DataToolError("Refusing to restore over the live database.")
    verify_database(backup)
    _copy_database(backup, target)
    return target
