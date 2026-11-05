"""FastAPI application for PayPilot AI."""

import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.analytics import ReconciliationAnalytics
from backend.auth import AuthRepository
from backend.config import get_settings
from backend.database import InvestigationRepository, ReconciliationRepository
from backend.evidence import (
    assess_capture_evidence,
    classify_order_evidence,
    select_order_evidence,
)
from backend.explainer import GeminiExplainer
from backend.paypal_client import PayPalAPIError, PayPalClient
from backend.reconciliation import MAX_CSV_BYTES, ReconciliationInputError
from backend.reconciliation_service import reconcile_csv


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and close the shared PayPal client."""
    settings = get_settings()
    client = PayPalClient(settings=settings)
    app.state.paypal_client = client
    app.state.gemini_explainer = GeminiExplainer(settings=settings)
    try:
        yield
    finally:
        client.close()


app = FastAPI(
    title="PayPilot AI",
    description="Read-only PayPal Sandbox payment investigation.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_paypal_client(request: Request) -> PayPalClient:
    """Return the application-scoped PayPal client."""
    return request.app.state.paypal_client


def get_gemini_explainer(request: Request) -> GeminiExplainer:
    """Return the application-scoped Gemini explainer."""
    return request.app.state.gemini_explainer


def get_auth_repository() -> AuthRepository:
    """Create and initialize the local authentication repository."""
    repository = AuthRepository()
    try:
        repository.initialize()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    return repository


SESSION_COOKIE = "paypilot_session"
SESSION_TTL_HOURS = 8
CSRF_HEADER = "X-CSRF-Token"
CSRF_PLACEHOLDER = "__CSRF_TOKEN__"
ROLE_PLACEHOLDER = "__USER_ROLE__"


class _LoginThrottle:
    """Small in-memory limiter for repeated failed sign-in attempts."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        recent = [
            t for t in self._failures.get(key, [])
            if now - t < self.window_seconds
        ]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.max_failures

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            now = time.monotonic()
            recent = self._recent(key, now)
            recent.append(now)
            self._failures[key] = recent

    def reset(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = _LoginThrottle()


class Credentials(BaseModel):
    """Strict JSON request for sign-in and first-owner setup."""

    model_config = ConfigDict(extra="forbid", strict=True)

    username: str
    password: str


def _current_session(request: Request, auth: AuthRepository) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    return auth.get_session(token) if token else None


def require_user(
    request: Request,
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict:
    """Require a valid signed-in session for the request."""
    try:
        session = _current_session(request, auth)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    if session is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication is required.",
        )
    return session


def require_csrf_user(
    request: Request,
    session: Annotated[dict, Depends(require_user)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict:
    """Require a signed-in session plus a valid CSRF token header."""
    token = request.headers.get(CSRF_HEADER, "")
    if not auth.validate_csrf(session["session_id"], token):
        raise HTTPException(
            status_code=403,
            detail="The CSRF token is missing or invalid. Reload the page.",
        )
    return session


def require_owner(
    session: Annotated[dict, Depends(require_user)],
) -> dict:
    """Require the signed-in user to be an owner."""
    if session["role"] != "owner":
        raise HTTPException(
            status_code=403,
            detail="Owner permission is required.",
        )
    return session


def require_owner_csrf(
    session: Annotated[dict, Depends(require_csrf_user)],
) -> dict:
    """Require an owner session plus a valid CSRF token."""
    return require_owner(session)


def _set_session_cookie(
    request: Request, response: Response, token: str
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


def get_investigation_repository() -> InvestigationRepository:
    """Create and initialize the local investigation repository."""
    repository = InvestigationRepository()
    try:
        repository.initialize()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Investigation history is temporarily unavailable.",
        ) from exc
    return repository


def get_reconciliation_repository() -> ReconciliationRepository:
    """Create and initialize the local reconciliation history repository."""
    repository = ReconciliationRepository()
    try:
        repository.initialize()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation history is temporarily unavailable.",
        ) from exc
    return repository


class ReconciliationRequest(BaseModel):
    """Strict JSON request for merchant CSV reconciliation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    csv_text: str


_FRONTEND_DIR = FilePath(__file__).resolve().parent.parent / "frontend"


@app.get("/", include_in_schema=False)
def serve_frontend(
    request: Request,
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
):
    """Serve the interface to signed-in users; others go to the login page."""
    session = _current_session(request, auth)
    if session is None:
        return RedirectResponse("/login", status_code=303)

    # Each page load receives a fresh CSRF token bound to this session.
    csrf_token = auth.rotate_csrf_token(session["session_id"])
    if csrf_token is None:
        return RedirectResponse("/login", status_code=303)

    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(CSRF_PLACEHOLDER, csrf_token).replace(
        ROLE_PLACEHOLDER, session["role"]
    )
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/login", include_in_schema=False)
def serve_login() -> FileResponse:
    """Serve the sign-in and first-owner setup page."""
    return FileResponse(
        _FRONTEND_DIR / "login.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/auth/status")
def auth_status(
    request: Request,
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Report whether setup is needed and whether the caller is signed in."""
    try:
        session = _current_session(request, auth)
        setup_required = not auth.has_owner()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    return {
        "setup_required": setup_required,
        "authenticated": session is not None,
        "username": session["username"] if session else None,
        "role": session["role"] if session else None,
    }


def _start_session(
    request: Request,
    response: Response,
    auth: AuthRepository,
    user_id: int,
) -> None:
    token, _csrf, _expires = auth.create_session(
        user_id, ttl_hours=SESSION_TTL_HOURS
    )
    _set_session_cookie(request, response, token)


@app.post("/api/auth/setup")
def auth_setup(
    credentials: Credentials,
    request: Request,
    response: Response,
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Create the first owner account (only while none exists) and sign in."""
    try:
        user_id = auth.create_initial_owner(
            credentials.username, credentials.password
        )
    except ValueError as exc:
        if "already exists" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Setup has already been completed. Sign in instead.",
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Setup has already been completed. Sign in instead.",
        ) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc

    _start_session(request, response, auth, user_id)
    return {"username": credentials.username.strip(), "role": "owner"}


@app.post("/api/auth/login")
def auth_login(
    credentials: Credentials,
    request: Request,
    response: Response,
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Sign in with a username and password."""
    client_host = request.client.host if request.client else "unknown"
    key = (credentials.username.strip().lower()[:64], client_host)

    if login_throttle.blocked(key):
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Try again in a few minutes.",
        )

    try:
        user = auth.authenticate(credentials.username, credentials.password)
        if user is None:
            login_throttle.record_failure(key)
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password.",
            )
        _start_session(request, response, auth, user["id"])
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc

    login_throttle.reset(key)
    return {"username": user["username"], "role": user["role"]}


@app.get("/api/auth/users")
def list_accounts(
    _owner: Annotated[dict, Depends(require_owner)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """List accounts (owner only)."""
    try:
        users = auth.list_users()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    return {"users": users, "count": len(users)}


@app.post("/api/auth/users", status_code=201)
def create_employee_account(
    credentials: Credentials,
    owner: Annotated[dict, Depends(require_owner_csrf)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Create an employee account (owner only; owners cannot be created here)."""
    try:
        user_id = auth.create_user(
            credentials.username,
            credentials.password,
            "employee",
            created_by=owner["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="That username is already in use.",
        ) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    return {
        "id": user_id,
        "username": credentials.username.strip(),
        "role": "employee",
    }


def _change_account_state(
    user_id: int, active: bool, owner: dict, auth: AuthRepository
) -> dict[str, object]:
    try:
        found = auth.set_user_active(user_id, active, owner["user_id"])
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication services are temporarily unavailable.",
        ) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Account was not found.")
    return {"id": user_id, "is_active": active}


@app.post("/api/auth/users/{user_id}/deactivate")
def deactivate_account(
    user_id: Annotated[int, Path(ge=1)],
    owner: Annotated[dict, Depends(require_owner_csrf)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Deactivate an employee and revoke their sessions (owner only)."""
    return _change_account_state(user_id, False, owner, auth)


@app.post("/api/auth/users/{user_id}/reactivate")
def reactivate_account(
    user_id: Annotated[int, Path(ge=1)],
    owner: Annotated[dict, Depends(require_owner_csrf)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, object]:
    """Reactivate an employee account (owner only)."""
    return _change_account_state(user_id, True, owner, auth)


@app.post("/api/auth/logout")
def auth_logout(
    request: Request,
    response: Response,
    _session: Annotated[dict, Depends(require_csrf_user)],
    auth: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> dict[str, bool]:
    """Revoke the current session and clear its cookie."""
    token = request.cookies.get(SESSION_COOKIE, "")
    auth.revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_out": True}


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {"status": "ok", "environment": "sandbox"}


@app.get("/api/investigations", dependencies=[Depends(require_user)])
def list_investigation_history(
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, object]:
    """Return recent investigation history without contacting PayPal."""
    try:
        investigations = repository.list_investigations(limit=limit)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="Investigation history is temporarily unavailable.",
        ) from exc

    return {
        "investigations": investigations,
        "count": len(investigations),
    }


@app.post("/api/reconciliation", dependencies=[Depends(require_csrf_user)])
async def reconcile_merchant_csv(
    request: Request,
    paypal: Annotated[PayPalClient, Depends(get_paypal_client)],
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
) -> dict[str, object]:
    """Compare merchant CSV records with PayPal Sandbox orders."""
    max_request_bytes = (MAX_CSV_BYTES * 2) + 4096

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_request_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="The reconciliation request is too large.",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="The request size header is invalid.",
            ) from exc

    body_chunks = []
    body_size = 0

    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > max_request_bytes:
            raise HTTPException(
                status_code=413,
                detail="The reconciliation request is too large.",
            )
        body_chunks.append(chunk)

    body = b"".join(body_chunks)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="A valid JSON request body is required.",
        ) from exc

    try:
        validated = ReconciliationRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="The request must contain only a string csv_text field.",
        ) from exc

    if len(validated.csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The CSV exceeds the 1 MB size limit.",
        )

    try:
        result = reconcile_csv(validated.csv_text, paypal)
    except ReconciliationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        for row in result["results"]:
            row["historical_matches"] = repository.find_previous_matches(
                merchant_reference=row["merchant_reference"],
                paypal_order_id=row["paypal_order_id"],
            )
            if (
                row["historical_matches"]
                and "historical_identifier_match" not in row["risk_signals"]
            ):
                row["risk_signals"].append("historical_identifier_match")

        result["run_id"] = repository.save_run(result)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation history is temporarily unavailable.",
        ) from exc

    return result


@app.get("/api/reconciliation/runs", dependencies=[Depends(require_user)])
def list_reconciliation_runs(
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, object]:
    """List saved reconciliation runs without contacting PayPal."""
    try:
        runs = repository.list_runs(limit=limit)
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation history is temporarily unavailable.",
        ) from exc

    return {"runs": runs, "count": len(runs)}


@app.get("/api/reconciliation/runs/{run_id}", dependencies=[Depends(require_user)])
def retrieve_reconciliation_run(
    run_id: Annotated[int, Path(ge=1)],
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
) -> dict[str, object]:
    """Retrieve a saved reconciliation run without contacting PayPal."""
    try:
        run = repository.get_run(run_id)
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation history is temporarily unavailable.",
        ) from exc

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Reconciliation run was not found.",
        )

    return run


@app.get("/api/analytics/summary", dependencies=[Depends(require_user)])
def get_analytics_summary(
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
) -> dict[str, object]:
    """Summarize saved reconciliation history without contacting PayPal."""
    try:
        return ReconciliationAnalytics(repository.database_path).summary()
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation analytics are temporarily unavailable.",
        ) from exc


@app.get("/api/analytics/trends", dependencies=[Depends(require_user)])
def get_analytics_trends(
    repository: Annotated[
        ReconciliationRepository, Depends(get_reconciliation_repository)
    ],
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, object]:
    """Return daily reconciliation trends from saved history."""
    try:
        return ReconciliationAnalytics(repository.database_path).trends(
            limit=limit
        )
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation analytics are temporarily unavailable.",
        ) from exc


@app.get("/api/analytics/investigations", dependencies=[Depends(require_user)])
def get_investigation_analytics(
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
) -> dict[str, object]:
    """Summarize saved PayPal investigations without contacting PayPal."""
    try:
        return ReconciliationAnalytics(
            repository.database_path
        ).investigation_summary()
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Investigation analytics are temporarily unavailable.",
        ) from exc


@app.get("/api/paypal/orders/{order_id}", dependencies=[Depends(require_user)])
def retrieve_paypal_order(
    order_id: Annotated[str, Path(min_length=1, max_length=128)],
    paypal: Annotated[PayPalClient, Depends(get_paypal_client)],
) -> dict:
    """Retrieve a PayPal Sandbox order without modifying it."""
    if not order_id.strip():
        raise HTTPException(
            status_code=422,
            detail="A non-empty PayPal order ID is required.",
        )

    try:
        order = paypal.get_order(order_id)
    except PayPalAPIError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="PayPal order was not found.",
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="PayPal could not complete the order lookup.",
        ) from exc
    except RuntimeError as exc:
        if "timed out" in str(exc).lower():
            raise HTTPException(
                status_code=504,
                detail="PayPal order lookup timed out.",
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="PayPal order lookup is temporarily unavailable.",
        ) from exc

    return select_order_evidence(order)


@app.get("/api/paypal/orders/{order_id}/investigation", dependencies=[Depends(require_user)])
def investigate_paypal_order(
    order_id: Annotated[str, Path(min_length=1, max_length=128)],
    paypal: Annotated[PayPalClient, Depends(get_paypal_client)],
    explainer: Annotated[
        GeminiExplainer, Depends(get_gemini_explainer)
    ],
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
) -> dict:
    """Retrieve and deterministically classify PayPal order evidence."""
    if not order_id.strip():
        raise HTTPException(
            status_code=422,
            detail="A non-empty PayPal order ID is required.",
        )

    try:
        order = paypal.get_order(order_id)
    except PayPalAPIError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="PayPal order was not found.",
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="PayPal could not complete the order investigation.",
        ) from exc
    except RuntimeError as exc:
        if "timed out" in str(exc).lower():
            raise HTTPException(
                status_code=504,
                detail="PayPal order investigation timed out.",
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="PayPal order investigation is temporarily unavailable.",
        ) from exc

    assessment = classify_order_evidence(order)
    evidence = select_order_evidence(order)
    try:
        repository.save_investigation(evidence, assessment)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="Investigation history is temporarily unavailable.",
        ) from exc

    capture_assessment = assess_capture_evidence(order)
    try:
        explanation = explainer.explain(order, assessment)
    except Exception:  # noqa: BLE001
        # AI explanations are optional; deterministic findings remain authoritative.
        explanation = {
            "status": "fallback",
            "explanation": assessment.get("summary")
            or "The order was assessed using deterministic evidence.",
        }

    return {
        "evidence": evidence,
        "assessment": assessment,
        "capture_assessment": capture_assessment,
        "explanation": explanation,
    }
