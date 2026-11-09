"""Restore a PayPilot backup to a NEW file for inspection.

Usage: python scripts/restore_db.py BACKUP_FILE [TARGET_FILE]

The live database is never overwritten. Without TARGET_FILE the copy is
written to the recovery directory. To put a restored copy into service, stop
the application and move the file into place yourself.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_tools import DataToolError, restore_backup


def main() -> None:
    args = sys.argv[1:]
    if not 1 <= len(args) <= 2:
        raise SystemExit(__doc__)
    target = Path(args[1]) if len(args) == 2 else None
    try:
        restored = restore_backup(Path(args[0]), target)
    except DataToolError as exc:
        raise SystemExit(f"Restore failed: {exc}") from exc
    print(f"Restored and verified: {restored}")


if __name__ == "__main__":
    main()
