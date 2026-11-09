# Runtime data layout, backup and recovery

PayPilot keeps all operational files under one data directory.

## Choosing the location

Set `PAYPILOT_DATA_DIR` (environment variable or `.env`) to an absolute path:

```
PAYPILOT_DATA_DIR=/data/paypilot-data
```

The application creates this tree on startup, readable only by its owner
(mode `0700`; the database and backups are `0600`):

```
/data/paypilot-data/
├── databases/paypilot.sqlite3   live database
├── backups/                     verified, timestamped backups
├── logs/                        reserved for application logs
├── exports/                     reserved for exports
└── recovery/                    restored copies for inspection
```

If `PAYPILOT_DATA_DIR` is unset, PayPilot uses the legacy in-repository
`data/paypilot.sqlite3` (git-ignored), so existing setups keep working.
A relative `PAYPILOT_DATA_DIR` is rejected.

## Moving an existing database

Stop the application, then **copy** (do not move) the database, and keep the
original until you have confirmed the app works from the new location:

```
mkdir -p /data/paypilot-data/databases
cp -p data/paypilot.sqlite3 /data/paypilot-data/databases/paypilot.sqlite3
chmod 700 /data/paypilot-data /data/paypilot-data/databases
chmod 600 /data/paypilot-data/databases/paypilot.sqlite3
```

## Backup

```
python scripts/backup_db.py
```

Uses SQLite's online backup API, so it is safe while the app is running. The
copy is integrity-checked before the command reports success, and a failed
copy is deleted rather than left behind.

## Restore (always to a new file)

```
python scripts/restore_db.py /data/paypilot-data/backups/<file>.sqlite3
```

The backup is integrity-checked and copied into `recovery/`. The script
refuses to overwrite any existing file and refuses to target the live
database. To put a restored copy into service: stop the app, move the live
database aside, then move the restored file into
`databases/paypilot.sqlite3`.

## Known limitations

- Backups are manual. Schedule `scripts/backup_db.py` yourself (for example
  with cron) and copy backups off the machine.
- Backups are not encrypted. They contain password hashes and investigation
  history; protect the directory accordingly.
- `logs/` and `exports/` are created but not yet written to by the app.

## Audit actor attribution and migration

`auth_audit_events` has an `actor_user_id` column recording which owner
created, deactivated or reactivated an account. Existing databases are
migrated automatically and safely on startup (`ALTER TABLE ... ADD COLUMN`,
guarded by a column check, so it is idempotent and keeps every existing row).
Events recorded before this change, self-service events such as login, and
the first owner's creation have no actor and show `actor: null`.

Before upgrading a database you care about, take a backup:
`python scripts/backup_db.py`.
