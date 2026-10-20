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
| POST | `/api/reconciliation` | Compares uploaded CSV records with PayPal Sandbox order evidence and saves the run |
| GET | `/api/reconciliation/runs?limit=50` | Lists saved reconciliation runs (limit: 1–500, default: 50) |
| GET | `/api/reconciliation/runs/{run_id}` | Retrieves one saved reconciliation run and its results |

Investigation and reconciliation endpoints are read-only. Reconciliation history
lets you review saved comparisons without repeating PayPal lookups. The default
database is stored at `data/paypilot.sqlite3`; tests use temporary databases.

A matching order amount and currency, or an order marked completed, is not
proof that payment settlement has been verified. Reconciliation results
explicitly keep `settlement_verified` false. This prototype does not capture,
refund, or move money.

### Reconciliation workflow

1. Open the web interface and submit a CSV file for reconciliation.
2. Review the comparison results and their statuses.
3. Use **Saved reconciliation runs** to refresh the history and open a prior run.
4. Treat results as comparison evidence only; settlement remains unverified.

Use PayPal Sandbox credentials and test data only. This development prototype
is not production-ready.

## Tests

Run the test suite from the repository root with the virtual environment
activated:

```bash
python -m pytest -q
```
The tests cover configuration, PayPal client behavior, evidence assessment,
AI explanation behavior, and API routes. Tests use controlled fixtures and
mocks where external services are involved.
