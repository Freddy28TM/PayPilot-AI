"""Shared test configuration.

Most API tests exercise business behavior, not authentication, so they run
with the authentication dependencies stubbed. Tests that verify real
authentication behavior opt out with ``@pytest.mark.real_auth``.
"""

import pytest

from backend import main

_FAKE_SESSION = {
    "session_id": 1,
    "user_id": 1,
    "username": "test.owner",
    "role": "owner",
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_auth: run the test with real authentication enabled",
    )


@pytest.fixture(autouse=True)
def _stub_authentication(request):
    if request.node.get_closest_marker("real_auth"):
        yield
        return

    main.app.dependency_overrides[main.require_user] = lambda: _FAKE_SESSION
    main.app.dependency_overrides[main.require_csrf_user] = (
        lambda: _FAKE_SESSION
    )
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(main.require_user, None)
        main.app.dependency_overrides.pop(main.require_csrf_user, None)


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    main.login_throttle._failures.clear()
    yield
    main.login_throttle._failures.clear()
