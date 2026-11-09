"""Runtime data locations.

Operational files (database, backups, logs, exports, recovery copies) live
under one data directory. Set ``PAYPILOT_DATA_DIR`` to keep them outside the
source repository, for example ``/data/paypilot-data``. When it is unset the
legacy in-repository ``data/`` location is used so existing setups keep
working unchanged.
"""

import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "PAYPILOT_DATA_DIR"
DATABASE_FILENAME = "paypilot.sqlite3"
LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class DataPaths:
    """Resolved runtime locations for one data directory."""

    root: Path
    databases: Path
    backups: Path
    logs: Path
    exports: Path
    recovery: Path
    legacy_layout: bool

    @property
    def database(self) -> Path:
        return self.databases / DATABASE_FILENAME

    def ensure_directories(self) -> None:
        """Create the directory tree; private to the owning user."""
        for directory in (
            self.root,
            self.databases,
            self.backups,
            self.logs,
            self.exports,
            self.recovery,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)


def _configured_root() -> str | None:
    value = os.environ.get(DATA_DIR_ENV)
    if value is None:
        # Fall back to the same .env file the rest of the settings use.
        from backend.config import Settings

        configured = Settings().paypilot_data_dir
        value = str(configured) if configured is not None else None
    if value is None or not value.strip():
        return None
    return value.strip()


def get_data_paths() -> DataPaths:
    """Resolve runtime paths from configuration."""
    configured = _configured_root()
    if configured is None:
        root = LEGACY_DATA_DIR
        return DataPaths(
            root=root,
            databases=root,
            backups=root / "backups",
            logs=root / "logs",
            exports=root / "exports",
            recovery=root / "recovery",
            legacy_layout=True,
        )

    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{DATA_DIR_ENV} must be an absolute path.")
    return DataPaths(
        root=root,
        databases=root / "databases",
        backups=root / "backups",
        logs=root / "logs",
        exports=root / "exports",
        recovery=root / "recovery",
        legacy_layout=False,
    )


def resolve_database_path() -> Path:
    """Return the live database path for the current configuration."""
    return get_data_paths().database
