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
Open http://127.0.0.1:8000/ for the interface. On first run you are sent to
a setup page to create the owner account (username plus a password of at
least 12 characters); afterwards you sign in with it.
Open http://127.0.0.1:8000/health to check the API.
PayPilot AI is restricted to PayPal Sandbox during setup. Use Sandbox
credentials and test data only.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Serves the web interface (redirects to `/login` when signed out) |
| GET | `/login` | Serves the sign-in / first-time setup page |
| GET | `/health` | Checks API availability |
| GET | `/api/auth/status` | Reports whether setup is required and whether the caller is signed in |
| POST | `/api/auth/setup` | Creates the first owner account (only while none exists) and signs in |
| POST | `/api/auth/login` | Signs in with a username and password |
| POST | `/api/auth/logout` | Revokes the current session (requires CSRF token) |
| GET | `/api/paypal/orders/{order_id}` | Retrieves a PayPal Sandbox order |
| GET | `/api/paypal/orders/{order_id}/investigation` | Returns selected evidence, deterministic assessments, and an explanation |
| POST | `/api/reconciliation` | Compares uploaded CSV records with PayPal Sandbox order evidence and saves the run |
| GET | `/api/reconciliation/runs?limit=50` | Lists saved reconciliation runs (limit: 1–500, default: 50) |
| GET | `/api/reconciliation/runs/{run_id}` | Retrieves one saved reconciliation run and its results |
| GET | `/api/analytics/summary` | Summarizes all saved reconciliation runs, status counts, record-match rate, record-count consistency, and risk-signal history availability |
| GET | `/api/analytics/trends?limit=30` | Returns daily reconciliation aggregates; `limit` is 1–365 and defaults to 30 |

All `/api/*` routes except `/api/auth/*` require a signed-in session. Investigation and reconciliation endpoints are read-only toward PayPal. Reconciliation history
lets you review saved comparisons without repeating PayPal lookups. The default
database is stored at `data/paypilot.sqlite3`; tests use temporary databases.

### Reconciliation analytics

The analytics endpoints summarize persisted reconciliation history and do not
contact PayPal. The summary includes saved-run and row totals, counts by
reconciliation status, exception rows, record-match rate, record-count
consistency, the latest saved run, and whether risk-signal history is available.

The trends endpoint returns daily aggregates for the most recent days that
contain saved runs. Its `limit` query parameter accepts integers from 1 to
365 and defaults to 30.

The record-match rate is the percentage of saved rows with status `matched`.
It measures agreement with the available order evidence, not payment settlement.
Both analytics endpoints explicitly return `settlement_verified: false`.

A matching order amount and currency, or an order marked completed, is not
proof that payment settlement has been verified. Reconciliation results
explicitly keep `settlement_verified` false. This prototype does not capture,
refund, or move money.

### Duplicate detection and risk signals

PayPilot AI identifies duplicate records within an uploaded reconciliation CSV and flags identifiers that match records from previous reconciliation runs.

Supported risk signals include:

- **Duplicate in file:** A merchant reference or PayPal order ID is repeated within the uploaded file. Duplicate rows are flagged for review rather than treated as independent confirmed payment records.
- **Historical identifier match:** An identifier matches a record found in an earlier reconciliation run. Review the earlier result and available evidence before drawing conclusions.

Risk signals are informational. A duplicate or historical identifier match does not, by itself, establish fraud, prove that a payment was attempted more than once, or confirm that money was received.

PayPal remains the source of truth for payment status. Reconciliation results and risk signals are evidence for human review, and `settlement_verified` remains `false`.

### Reconciliation workflow

**Sandbox-only warning:** Before uploading a CSV, confirm that PayPilot is
configured with PayPal Sandbox credentials and use test data only. Do not use
production credentials or real customer payment data. This prototype is not
production-ready.

1. Open the web interface and upload a CSV file for reconciliation.
2. Review matching records, mismatches, duplicate records, and associated risk signals.
3. Examine the evidence and explanations before deciding what requires further investigation.
4. Use **Saved reconciliation runs** to review previous comparisons and investigate historical identifier matches.
5. Treat all results as comparison evidence; settlement remains unverified.

## Security and release limitations

PayPilot AI is a local development prototype, not a production payment
operations service.

- **Authentication.** All data routes require a signed-in session. Passwords
  are hashed with scrypt; session tokens and CSRF tokens are stored only as
  SHA-256 hashes; the session cookie is `HttpOnly` and `SameSite=Strict`
  (and `Secure` over HTTPS); sessions expire after 8 hours; the state-changing
  reconciliation upload and logout require a per-page-load CSRF token; failed
  sign-ins are audited and throttled (5 failures per username and client
  address per 5 minutes, held in memory). Only one account (the owner) can be
  created through the interface; there is no role-based authorization,
  password reset, multi-factor authentication, or user management yet.
- First-time setup is open to whoever reaches the server first. Complete it
  yourself before exposing the app to anyone else, and do not expose it to the
  public internet or an untrusted network without TLS and further review.
- Use PayPal Sandbox credentials and synthetic test data only. The Sandbox
  configuration check is not a substitute for a production security review.
- Keep `.env` local and never commit or share API credentials.
- Before any production deployment, implement and test appropriate
  role-based authorization, multi-factor authentication, secret management, secure
  deployment configuration, audit logging, and operational monitoring.
- Review privacy, data-retention, backup, and recovery requirements before
  storing real customer or payment-related information.
- Automated tests and security scans provide useful evidence but do not
  establish production readiness. The vulnerability audit currently checks
  the listed pinned packages with dependency resolution disabled, so
  transitive dependencies require a separate complete audit.

## Tests

Run the test suite from the repository root with the virtual environment
activated:

```bash
python -m pytest -q
```
The tests cover configuration, PayPal client behavior, evidence assessment,
AI explanation behavior, API routes, reconciliation history, risk signals, and
analytics. Tests use controlled fixtures and mocks where external services
are involved.

Run the frontend tests with Node.js 20 or a compatible version:

```bash
node --test tests/frontend_reconciliation.test.cjs
node --test tests/frontend_history.test.cjs
```

The frontend tests use Node.js's built-in test runner; npm is not required.

## License

PayPilot AI is released under the [Apache License 2.0](LICENSE).
