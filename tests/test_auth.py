"""Isolated tests for authentication persistence and cryptographic primitives."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from backend.auth import AuthRepository, _hash_password, _verify_password


@pytest.fixture
def auth(tmp_path):
    repository = AuthRepository(tmp_path / "auth-test.sqlite3")
    repository.initialize()
    return repository


def test_password_hash_is_salted_and_verifiable():
    password = "A-long-test-password-2026"

    first_hash = _hash_password(password)
    second_hash = _hash_password(password)

    assert first_hash != second_hash
    assert _verify_password(password, first_hash)
    assert _verify_password(password, second_hash)
    assert not _verify_password("a-different-password", first_hash)


def test_password_length_is_enforced():
    with pytest.raises(ValueError):
        _hash_password("short")

    with pytest.raises(ValueError):
        _hash_password("x" * 1025)


def test_invalid_password_hash_is_rejected():
    assert not _verify_password("some-password", "not-a-valid-hash")
    assert not _verify_password("some-password", "bcrypt$invalid")


@pytest.mark.parametrize(
    "encoded",
    [
        "scrypt$1073741824$8$1$c2FsdA==$" + "A" * 88,
        "scrypt$16384$999999$1$c2FsdA==$" + "A" * 88,
        "scrypt$16384$8$1$%%%$" + "A" * 88,
        "scrypt$16384$8$1$c2FsdA==$AQ==",
        "scrypt$16384$8$1$c2FsdA==$" + "A" * 88,
    ],
)
def test_password_verifier_rejects_unsafe_or_malformed_hashes(encoded):
    assert not _verify_password("some-password", encoded)


def test_password_verifier_rejects_non_string_inputs():
    assert not _verify_password(None, "not-a-hash")
    assert not _verify_password("some-password", None)
    assert not _verify_password("x" * 1025, "not-a-hash")


def test_create_user_stores_hash_not_plaintext(auth, tmp_path):
    password = "Owner-test-password-2026"
    user_id = auth.create_user("owner.test", password, "owner")

    user = auth.get_user_for_login("OWNER.TEST")

    assert user is not None
    assert user["id"] == user_id
    assert user["role"] == "owner"
    assert user["is_active"] == 1
    assert user["password_hash"] != password
    assert _verify_password(password, user["password_hash"])

    database = tmp_path / "auth-test.sqlite3"
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT password_hash FROM auth_users WHERE id = ?",
            (user_id,),
        ).fetchone()[0]

    assert password not in stored_hash


def test_duplicate_username_is_rejected_case_insensitively(auth):
    auth.create_user("Owner.Test", "Owner-test-password-2026", "owner")

    with pytest.raises(sqlite3.IntegrityError):
        auth.create_user("owner.test", "Another-test-password-2026", "employee")


@pytest.mark.parametrize(
    ("username", "role"),
    [
        ("ab", "owner"),
        ("invalid username", "owner"),
        ("valid.user", "administrator"),
    ],
)
def test_invalid_account_details_are_rejected(auth, username, role):
    with pytest.raises(ValueError):
        auth.create_user(username, "Owner-test-password-2026", role)


def test_session_token_is_not_stored_in_plaintext(auth, tmp_path):
    user_id = auth.create_user(
        "session.owner",
        "Owner-test-password-2026",
        "owner",
    )

    token, csrf_token, expires_at = auth.create_session(user_id)

    session = auth.get_session(token)

    assert session is not None
    assert session["user_id"] == user_id
    assert session["username"] == "session.owner"
    assert session["role"] == "owner"
    assert session["expires_at"] == expires_at

    with sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection:
        stored_token_hash = connection.execute(
            "SELECT token_hash FROM auth_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

    assert token not in stored_token_hash
    assert len(csrf_token) >= 32


def test_session_rejects_unknown_token(auth):
    assert auth.get_session("unknown-session-token") is None


def test_session_expiry_is_enforced(auth, tmp_path):
    user_id = auth.create_user(
        "expired.owner",
        "Owner-test-password-2026",
        "owner",
    )
    token, _, _ = auth.create_session(user_id)

    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection:
        connection.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE token_hash = "
            "lower(hex(zeroblob(0)))",
            (expired_at,),
        )
        # Locate the session by its SHA-256 token digest.
        import hashlib

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE token_hash = ?",
            (expired_at, token_hash),
        )

    assert auth.get_session(token) is None


def test_csrf_token_must_match_session(auth):
    user_id = auth.create_user(
        "csrf.owner",
        "Owner-test-password-2026",
        "owner",
    )
    token, csrf_token, _ = auth.create_session(user_id)
    session = auth.get_session(token)

    assert session is not None
    assert auth.validate_csrf(session["session_id"], csrf_token)
    assert not auth.validate_csrf(session["session_id"], "wrong-token")
    assert not auth.validate_csrf(session["session_id"], "")


def test_revoked_session_cannot_be_reused(auth):
    user_id = auth.create_user(
        "revoke.owner",
        "Owner-test-password-2026",
        "owner",
    )
    token, csrf_token, _ = auth.create_session(user_id)
    session = auth.get_session(token)

    assert session is not None
    assert auth.revoke_session(token)
    assert not auth.revoke_session(token)
    assert auth.get_session(token) is None
    assert not auth.validate_csrf(session["session_id"], csrf_token)


def test_disabled_account_cannot_create_or_use_sessions(auth, tmp_path):
    user_id = auth.create_user(
        "disabled.owner",
        "Owner-test-password-2026",
        "owner",
    )
    token, _, _ = auth.create_session(user_id)

    with sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection:
        connection.execute(
            "UPDATE auth_users SET is_active = 0 WHERE id = ?",
            (user_id,),
        )

    assert auth.get_session(token) is None

    with pytest.raises(ValueError, match="active account"):
        auth.create_session(user_id)


def test_login_audit_does_not_store_password_or_token(auth, tmp_path):
    user_id = auth.create_user(
        "audit.owner",
        "Owner-test-password-2026",
        "owner",
    )
    auth.record_login("audit.owner", True, user_id)
    auth.record_login("unknown.user", False)

    with sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection:
        events = connection.execute(
            """
            SELECT username, event_type, succeeded
            FROM auth_audit_events
            ORDER BY id
            """
        ).fetchall()

    assert events[-2:] == [
        ("audit.owner", "login", 1),
        ("unknown.user", "login", 0),
    ]


def test_foreign_key_enforcement_rejects_orphan_session(auth, tmp_path):
    with (
        sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
                """
                INSERT INTO auth_sessions (
                    user_id, token_hash, csrf_hash, created_at, expires_at
                )
                VALUES (99999, 'token-hash', 'csrf-hash', 'now', 'later')
                """
            )


def test_initialization_is_idempotent(auth):
    auth.initialize()
    auth.initialize()

    user_id = auth.create_user(
        "repeat.owner",
        "Owner-test-password-2026",
        "owner",
    )

    assert auth.get_user_for_login("repeat.owner")["id"] == user_id


def test_create_user_normalizes_username_before_validation_and_storage(auth, tmp_path):
    user_id = auth.create_user(
        "  normalized.owner  ",
        "Owner-test-password-2026",
        "owner",
    )

    user = auth.get_user_for_login("normalized.owner")
    assert user is not None
    assert user["id"] == user_id
    assert user["username"] == "normalized.owner"

    with sqlite3.connect(tmp_path / "auth-test.sqlite3") as connection:
        audit_username = connection.execute(
            """
            SELECT username FROM auth_audit_events
            WHERE user_id = ? AND event_type = 'account_created'
            """,
            (user_id,),
        ).fetchone()[0]

    assert audit_username == "normalized.owner"


def test_create_user_rejects_non_string_role(auth):
    with pytest.raises(ValueError, match="Role must be owner or employee"):
        auth.create_user(
            "invalid.role",
            "Owner-test-password-2026",
            [],
        )


def test_session_and_csrf_reject_oversized_tokens(auth):
    user_id = auth.create_user(
        "bounded.tokens",
        "Owner-test-password-2026",
        "owner",
    )
    token, csrf_token, _ = auth.create_session(user_id)
    session = auth.get_session(token)

    assert session is not None
    oversized = "x" * 257

    assert auth.get_session(oversized) is None
    assert not auth.validate_csrf(session["session_id"], oversized)
    assert not auth.revoke_session(oversized)

    # Normal generated tokens must continue to work.
    assert auth.validate_csrf(session["session_id"], csrf_token)


def test_initial_owner_can_only_be_created_once(auth):
    owner_id = auth.create_initial_owner(
        "first.owner",
        "Strong-Test-Password-2026!",
    )

    assert owner_id > 0

    owner = auth.get_user_for_login("first.owner")
    assert owner is not None
    assert owner["role"] == "owner"
    assert owner["is_active"] == 1

    with pytest.raises(ValueError, match="already exists"):
        auth.create_initial_owner(
            "second.owner",
            "Another-Strong-Password-2026!",
        )

    assert auth.get_user_for_login("second.owner") is None


def test_csrf_token_rotation_invalidates_previous_token(auth):
    user_id = auth.create_user(
        "csrf.owner",
        "Strong-Test-Password-2026!",
        "owner",
    )

    session_token, original_csrf, _ = auth.create_session(user_id)
    session = auth.get_session(session_token)

    assert session is not None
    assert auth.validate_csrf(session["session_id"], original_csrf)

    rotated_csrf = auth.rotate_csrf_token(session["session_id"])

    assert rotated_csrf is not None
    assert rotated_csrf != original_csrf
    assert not auth.validate_csrf(session["session_id"], original_csrf)
    assert auth.validate_csrf(session["session_id"], rotated_csrf)


def test_csrf_rotation_rejects_revoked_session(auth):
    user_id = auth.create_user(
        "revoked.owner",
        "Strong-Test-Password-2026!",
        "owner",
    )

    session_token, _, _ = auth.create_session(user_id)
    session = auth.get_session(session_token)

    assert session is not None
    assert auth.revoke_session(session_token)

    assert auth.rotate_csrf_token(session["session_id"]) is None
