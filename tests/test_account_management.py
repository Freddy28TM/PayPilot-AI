"""Owner/employee authorization and account-management tests."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.auth import AuthRepository
from backend.main import app, get_auth_repository

pytestmark = pytest.mark.real_auth

PASSWORD = "Strong-Test-Password-2026!"
EMPLOYEE_PASSWORD = "Employee-Password-2026!"


@pytest.fixture
def clients(tmp_path):
    repository = AuthRepository(tmp_path / "auth.sqlite3")
    repository.initialize()
    app.dependency_overrides[get_auth_repository] = lambda: repository
    try:
        with (
            TestClient(app, follow_redirects=False) as owner,
            TestClient(app, follow_redirects=False) as employee,
        ):
            owner.repository = repository
            owner.post(
                "/api/auth/setup",
                json={"username": "the.owner", "password": PASSWORD},
            ).raise_for_status()
            yield owner, employee
    finally:
        app.dependency_overrides.clear()


def csrf(client):
    page = client.get("/")
    assert page.status_code == 200
    return re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)


def headers(client):
    return {main.CSRF_HEADER: csrf(client)}


def make_employee(owner, username="emp.one"):
    response = owner.post(
        "/api/auth/users",
        json={"username": username, "password": EMPLOYEE_PASSWORD},
        headers=headers(owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign_in_employee(employee, username="emp.one"):
    response = employee.post(
        "/api/auth/login",
        json={"username": username, "password": EMPLOYEE_PASSWORD},
    )
    assert response.status_code == 200
    return response


def test_owner_creates_employee_who_can_use_business_routes(clients):
    owner, employee = clients

    created = make_employee(owner)
    assert created["role"] == "employee"

    sign_in_employee(employee)
    assert employee.get("/api/investigations").status_code == 200
    assert employee.get("/api/auth/status").json()["role"] == "employee"


def test_created_accounts_are_always_employees(clients):
    owner, _ = clients

    response = owner.post(
        "/api/auth/users",
        json={"username": "sneaky", "password": EMPLOYEE_PASSWORD, "role": "owner"},
        headers=headers(owner),
    )

    assert response.status_code == 422
    assert owner.repository.get_user_for_login("sneaky") is None


def test_employee_cannot_manage_accounts(clients):
    owner, employee = clients
    make_employee(owner)
    sign_in_employee(employee)

    assert employee.get("/api/auth/users").status_code == 403
    created = employee.post(
        "/api/auth/users",
        json={"username": "evil.one", "password": EMPLOYEE_PASSWORD},
        headers=headers(employee),
    )
    assert created.status_code == 403
    assert (
        employee.post(
            "/api/auth/users/1/deactivate", headers=headers(employee)
        ).status_code
        == 403
    )
    assert owner.repository.get_user_for_login("evil.one") is None


def test_account_management_requires_login_and_csrf(clients):
    owner, employee = clients

    assert employee.get("/api/auth/users").status_code == 401
    assert (
        employee.post(
            "/api/auth/users",
            json={"username": "no.login", "password": EMPLOYEE_PASSWORD},
        ).status_code
        == 401
    )
    # Signed in, but without a CSRF token.
    assert (
        owner.post(
            "/api/auth/users",
            json={"username": "no.csrf", "password": EMPLOYEE_PASSWORD},
        ).status_code
        == 403
    )


def test_listing_never_exposes_credentials(clients):
    owner, _ = clients
    make_employee(owner)

    body = owner.get("/api/auth/users").json()

    assert body["count"] == 2
    assert {u["username"] for u in body["users"]} == {"the.owner", "emp.one"}
    for user in body["users"]:
        assert set(user) == {"id", "username", "role", "is_active", "created_at"}


def test_duplicate_and_weak_employee_input(clients):
    owner, _ = clients
    make_employee(owner)

    duplicate = owner.post(
        "/api/auth/users",
        json={"username": "EMP.ONE", "password": EMPLOYEE_PASSWORD},
        headers=headers(owner),
    )
    weak = owner.post(
        "/api/auth/users",
        json={"username": "weak.pw", "password": "short"},
        headers=headers(owner),
    )

    assert duplicate.status_code == 409
    assert weak.status_code == 422


def test_deactivation_blocks_login_and_revokes_live_session(clients):
    owner, employee = clients
    created = make_employee(owner)
    sign_in_employee(employee)
    assert employee.get("/api/investigations").status_code == 200

    response = owner.post(
        f"/api/auth/users/{created['id']}/deactivate", headers=headers(owner)
    )

    assert response.status_code == 200
    assert response.json() == {"id": created["id"], "is_active": False}
    assert employee.get("/api/investigations").status_code == 401
    employee.cookies.clear()
    relogin = employee.post(
        "/api/auth/login",
        json={"username": "emp.one", "password": EMPLOYEE_PASSWORD},
    )
    assert relogin.status_code == 401


def test_reactivation_restores_access(clients):
    owner, employee = clients
    created = make_employee(owner)
    owner.post(
        f"/api/auth/users/{created['id']}/deactivate", headers=headers(owner)
    )

    response = owner.post(
        f"/api/auth/users/{created['id']}/reactivate", headers=headers(owner)
    )

    assert response.status_code == 200
    sign_in_employee(employee)
    assert employee.get("/api/investigations").status_code == 200


def test_owner_cannot_deactivate_self_or_other_owner(clients):
    owner, _ = clients
    owner_id = owner.repository.get_user_for_login("the.owner")["id"]

    own = owner.post(
        f"/api/auth/users/{owner_id}/deactivate", headers=headers(owner)
    )

    assert own.status_code == 403
    assert owner.get("/api/investigations").status_code == 200

    # A second owner (created out-of-band) is also protected.
    other = owner.repository.create_user("other.owner", PASSWORD, "owner")
    protected = owner.post(
        f"/api/auth/users/{other}/deactivate", headers=headers(owner)
    )
    assert protected.status_code == 403


def test_unknown_account_returns_404(clients):
    owner, _ = clients

    response = owner.post(
        "/api/auth/users/9999/deactivate", headers=headers(owner)
    )

    assert response.status_code == 404


def test_account_changes_are_audited(clients):
    owner, _ = clients
    created = make_employee(owner)
    owner.post(
        f"/api/auth/users/{created['id']}/deactivate", headers=headers(owner)
    )
    owner.post(
        f"/api/auth/users/{created['id']}/reactivate", headers=headers(owner)
    )

    with sqlite3.connect(owner.repository.database_path) as connection:
        events = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM auth_audit_events "
                "WHERE username = 'emp.one' ORDER BY id"
            )
        ]

    assert events == [
        "account_created",
        "account_deactivated",
        "account_reactivated",
    ]


def test_homepage_exposes_role_to_the_page(clients):
    owner, _ = clients

    page = owner.get("/")

    assert 'name="user-role" content="owner"' in page.text
