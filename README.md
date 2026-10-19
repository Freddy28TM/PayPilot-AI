# PayPilot AI

**An AI Payment Operations Copilot for small businesses.**

PayPilot AI helps investigate confusing PayPal Sandbox orders by organizing
available evidence, applying deterministic checks, and explaining safe next
steps.

## Core principles

- PayPal remains the source of truth for payment status.
- Deterministic backend logic classifies the available evidence.
- AI explains validated findings; it does not invent transaction facts.
- Missing evidence is identified instead of treated as proof of settlement.
- The prototype does not autonomously capture, refund, or move money.

## Project status

This is a development prototype. Testing uses synthetic fixtures and PayPal
Sandbox. It is not production-ready and does not claim verified customer
demand.

## Setup

Requirements: Python and a virtual environment.

From the repository root, run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-lock.txt
cp .env.example .env
uvicorn backend.main:app --reload
```
Before starting the server, edit .env locally and add your PayPal
Developer Sandbox credentials. Configure an AI provider key if you want
AI-generated explanations. Never commit .env or share its secrets.
Open http://127.0.0.1:8000/ for the interface.
Open http://127.0.0.1:8000/health to check the API.
PayPilot AI is restricted to PayPal Sandbox during setup. Use Sandbox
credentials and test data only.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Serves the web interface |
| GET | `/health` | Checks API availability |
| GET | `/api/paypal/orders/{order_id}` | Retrieves a PayPal Sandbox order |
| GET | `/api/paypal/orders/{order_id}/investigation` | Returns selected evidence, deterministic assessments, and an explanation |

The investigation endpoint is read-only. An order marked completed is not,
by itself, proof that settlement has been verified.

## Tests

Run the test suite from the repository root with the virtual environment
activated:

```bash
python -m pytest -q
```
The tests cover configuration, PayPal client behavior, evidence assessment,
AI explanation behavior, and API routes. Tests use controlled fixtures and
mocks where external services are involved.
