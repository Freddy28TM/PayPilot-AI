"""End-to-end tests for authentication routes and route protection."""

import re

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.auth import AuthRepository
from backend.main import app, get_auth_repository

pytestmark = pytest.mark.real_auth

PASSWORD = "Strong-Test-Password-2026!"


@pytest.fixture
def client(tmp_path):
    repository = AuthRepository(tmp_path / "auth.sqlite3")
    repository.initialize()
    app.dependency_overrides[get_auth_repository] = lambda: repository
    try:
        with TestClient(app, follow_redirects=False) as test_client:
            test_client.repository = repository
            yield test_client
    finally:
        app.dependency_overrides.clear()


def setup_owner(client, username="owner.one"):
    response = client.post(
        "/api/auth/setup", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response


def page_csrf_token(client):
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match and match.group(1) != main.CSRF_PLACEHOLDER
    return match.group(1)


PROTECTED_GETS = [
    "/api/investigations",
    "/api/reconciliation/runs",
    "/api/reconciliation/runs/1",
    "/api/analytics/summary",
    "/api/analytics/trends",
    "/api/analytics/investigations",
    "/api/paypal/orders/ORDER-1",
    "/api/paypal/orders/ORDER-1/investigation",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_get_routes_require_login(client, path):
    assert client.get(path).status_code == 401


def test_reconciliation_post_requires_login(client):
    response = client.post("/api/reconciliation", json={"csv_text": "x"})
    assert response.status_code == 401


def test_public_routes_stay_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/api/auth/status").status_code == 200


def test_status_reports_setup_required_then_complete(client):
    assert client.get("/api/auth/status").json() == {
        "setup_required": True,
        "authenticated": False,
        "username": None,
        "role": None,
    }

    setup_owner(client)

    status = client.get("/api/auth/status").json()
    assert status["setup_required"] is False
    assert status["authenticated"] is True
    assert status["username"] == "owner.one"
    assert status["role"] == "owner"


def test_setup_works_only_once(client):
    setup_owner(client)

    second = client.post(
        "/api/auth/setup",
        json={"username": "attacker", "password": PASSWORD},
    )

    assert second.status_code == 409
    assert client.repository.get_user_for_login("attacker") is None


def test_setup_rejects_weak_input(client):
    short = client.post(
        "/api/auth/setup", json={"username": "owner.one", "password": "short"}
    )
    bad_name = client.post(
        "/api/auth/setup", json={"username": "a b", "password": PASSWORD}
    )
    extra = client.post(
        "/api/auth/setup",
        json={"username": "owner.one", "password": PASSWORD, "role": "owner"},
    )

    assert short.status_code == 422
    assert bad_name.status_code == 422
    assert extra.status_code == 422
    assert client.get("/api/auth/status").json()["setup_required"] is True


def test_session_cookie_is_hardened(client):
    response = setup_owner(client)

    cookie = response.headers["set-cookie"].lower()
    assert "paypilot_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_login_success_and_protected_access(client):
    setup_owner(client)
    client.cookies.clear()

    assert client.get("/api/investigations").status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"username": "OWNER.ONE", "password": PASSWORD},
    )

    assert login.status_code == 200
    assert login.json() == {"username": "owner.one", "role": "owner"}
    assert client.get("/api/investigations").status_code == 200


def test_login_failures_are_generic(client):
    setup_owner(client)
    client.cookies.clear()

    wrong_password = client.post(
        "/api/auth/login",
        json={"username": "owner.one", "password": "Wrong-Password-2026!"},
    )
    unknown_user = client.post(
        "/api/auth/login",
        json={"username": "nobody.here", "password": PASSWORD},
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_inactive_user_cannot_login(client):
    client.repository.create_user("disabled.user", PASSWORD, "employee")
    import sqlite3

    with sqlite3.connect(client.repository.database_path) as connection:
        connection.execute(
            "UPDATE auth_users SET is_active = 0 WHERE username = 'disabled.user'"
        )

    response = client.post(
        "/api/auth/login",
        json={"username": "disabled.user", "password": PASSWORD},
    )

    assert response.status_code == 401


def test_repeated_failed_logins_are_throttled(client):
    setup_owner(client)
    client.cookies.clear()
    bad = {"username": "owner.one", "password": "Wrong-Password-2026!"}

    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401

    assert client.post("/api/auth/login", json=bad).status_code == 429
    # Even the correct password is refused while the limit is active.
    good = {"username": "owner.one", "password": PASSWORD}
    assert client.post("/api/auth/login", json=good).status_code == 429


def test_login_events_are_audited(client):
    import sqlite3

    setup_owner(client)
    client.cookies.clear()
    client.post(
        "/api/auth/login",
        json={"username": "owner.one", "password": "Wrong-Password-2026!"},
    )
    client.post(
        "/api/auth/login", json={"username": "owner.one", "password": PASSWORD}
    )

    with sqlite3.connect(client.repository.database_path) as connection:
        rows = connection.execute(
            "SELECT succeeded FROM auth_audit_events "
            "WHERE event_type = 'login' ORDER BY id"
        ).fetchall()

    assert rows == [(0,), (1,)]


def test_homepage_redirects_when_signed_out(client):
    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_homepage_serves_frontend_with_csrf_token(client):
    setup_owner(client)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    for fragment in (
        'id="investigation-form"',
        'id="investigation-history"',
        'id="history-list"',
        'id="history-message"',
        "/api/investigations",
        'id="logout-button"',
    ):
        assert fragment in response.text
    assert main.CSRF_PLACEHOLDER not in response.text


def test_reconciliation_post_requires_valid_csrf_token(client):
    setup_owner(client)
    token = page_csrf_token(client)

    missing = client.post("/api/reconciliation", json={"csv_text": "x"})
    wrong = client.post(
        "/api/reconciliation",
        json={"csv_text": "x"},
        headers={main.CSRF_HEADER: "not-the-token"},
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403

    # A valid token passes the CSRF check (later layers handle the content).
    ok = client.post(
        "/api/reconciliation",
        json={"csv_text": ""},
        headers={main.CSRF_HEADER: token},
    )
    assert ok.status_code not in {401, 403}


def test_new_page_load_invalidates_previous_csrf_token(client):
    setup_owner(client)
    old = page_csrf_token(client)
    page_csrf_token(client)

    response = client.post(
        "/api/reconciliation",
        json={"csv_text": ""},
        headers={main.CSRF_HEADER: old},
    )

    assert response.status_code == 403


def test_logout_requires_csrf_and_revokes_session(client):
    setup_owner(client)

    assert client.post("/api/auth/logout").status_code == 403
    assert client.get("/api/investigations").status_code == 200

    token = page_csrf_token(client)
    response = client.post(
        "/api/auth/logout", headers={main.CSRF_HEADER: token}
    )

    assert response.status_code == 200
    assert response.json() == {"signed_out": True}
    assert client.get("/api/investigations").status_code == 401


def test_revoked_cookie_is_rejected_even_if_replayed(client):
    setup_owner(client)
    stolen = client.cookies.get("paypilot_session")
    token = page_csrf_token(client)
    client.post("/api/auth/logout", headers={main.CSRF_HEADER: token})

    client.cookies.set("paypilot_session", stolen)

    assert client.get("/api/investigations").status_code == 401


def test_auth_database_failure_returns_503(client):
    class Broken:
        def initialize(self):
            raise OSError("controlled failure")

    app.dependency_overrides.pop(get_auth_repository, None)
    original = main.AuthRepository
    main.AuthRepository = Broken
    try:
        assert client.get("/api/auth/status").status_code == 503
    finally:
        main.AuthRepository = original


def test_authenticate_rejects_non_string_input():
    repository = AuthRepository(":memory:")
    assert repository.authenticate(None, PASSWORD) is None
    assert repository.authenticate("x" * 100, PASSWORD) is None
