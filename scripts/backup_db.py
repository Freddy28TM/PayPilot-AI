"""Create a verified backup of the PayPilot database."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_tools import DataToolError, create_backup


def main() -> None:
    try:
        backup = create_backup()
    except DataToolError as exc:
        raise SystemExit(f"Backup failed: {exc}") from exc
    print(f"Backup written and verified: {backup}")


if __name__ == "__main__":
    main()
