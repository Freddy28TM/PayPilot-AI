"""FastAPI application for PayPilot AI."""

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.analytics import ReconciliationAnalytics
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


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    """Serve the PayPilot AI investigation interface."""
    frontend_path = (
        FilePath(__file__).resolve().parent.parent
        / "frontend"
        / "index.html"
    )
    return FileResponse(frontend_path)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {"status": "ok", "environment": "sandbox"}


@app.get("/api/investigations")
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


@app.post("/api/reconciliation")
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


@app.get("/api/reconciliation/runs")
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


@app.get("/api/reconciliation/runs/{run_id}")
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


@app.get("/api/analytics/summary")
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


@app.get("/api/analytics/trends")
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


@app.get("/api/analytics/investigations")
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


@app.get("/api/paypal/orders/{order_id}")
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


@app.get("/api/paypal/orders/{order_id}/investigation")
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
    except Exception:
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
