"""FastAPI application for PayPilot AI."""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse

from backend.config import get_settings
from backend.database import InvestigationRepository
from backend.evidence import (
    assess_capture_evidence,
    classify_order_evidence,
    select_order_evidence,
)
from backend.explainer import GeminiExplainer
from backend.paypal_client import PayPalAPIError, PayPalClient


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
    repository.initialize()
    return repository


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
    explanation = explainer.explain(order, assessment)

    return {
        "evidence": evidence,
        "assessment": assessment,
        "capture_assessment": capture_assessment,
        "explanation": explanation,
    }
