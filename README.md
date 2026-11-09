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

## Demo video

[Watch the 2-minute demo](https://youtu.be/r8ykCScLdS0): CSV reconciliation,
mismatch detection, the investigation view with an AI explanation, and team
accounts.

## How PayPilot uses PayPal

PayPilot integrates the PayPal Orders v2 REST API in the **Sandbox**
environment only (live credentials are rejected at startup):

| Purpose | Call |
| --- | --- |
| Authenticate | `POST /v1/oauth2/token` (client credentials) |
| Read an order's amount, currency and status | `GET /v2/checkout/orders/{id}` |
| Create a $1.00 USD test order (helper script only) | `POST /v2/checkout/orders` |

The app is **read-only toward PayPal**. It never captures, refunds or
modifies an order, and `settlement_verified` is always `false` because an
order lookup alone cannot prove settlement.

## How PayPilot uses AI

Every finding is classified by deterministic code first. AI is then asked
only to explain that finding in plain language and suggest a safe next step.
It cannot change the verdict. The explanation providers are tried in order:

1. OpenRouter (`OPENROUTER_API_KEY`, model `openrouter/free` by default)
2. Google Gemini (`GEMINI_API_KEY`, model `gemini-2.5-flash` by default)
3. A built-in deterministic explanation, used when no key is set or a
   provider fails

The structure of every AI response is validated before it is shown. No AI key
is required to run the app or the tests.

## Technology

Python, FastAPI and Uvicorn; SQLite; vanilla HTML and JavaScript (no build
step); PayPal Sandbox Orders v2; OpenRouter and Google Gemini; pytest, ruff,
bandit and pip-audit for quality and security checks.

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

## Try it in five minutes

You need a free PayPal Developer account with a Sandbox REST app (client ID
and secret). No AI key is required.

1. Follow **Setup** above, adding the Sandbox client ID and secret to `.env`.
2. Create a test order and note the ID it prints:
   ```bash
   python scripts/create_sandbox_test_order.py
   # prints a line starting "Sandbox order ID: "
   ```
3. Start the app, open http://127.0.0.1:8000/ and create the owner account.
4. Save this as `demo.csv`, replacing `ORDER_ID` with the ID from step 2
   (the helper creates a $1.00 USD order):
   ```csv
   merchant_reference,paypal_order_id,expected_amount,currency_code
   INV-1001,ORDER_ID,1.00,USD
   INV-1002,ORDER_ID,5.00,USD
   INV-1003,ORDER_ID,1.00,EUR
   ```
5. Upload it. Because rows repeat one order ID, all three are flagged as
   duplicates needing review. For a clean result, create two more orders
   and give each row its own ID: the 1.00 USD row matches, a changed amount
   shows an amount mismatch, and a changed currency shows a currency mismatch.
6. Open an investigation to see the deterministic evidence and the AI (or
   fallback) explanation.

**CSV format:** the header must be exactly
`merchant_reference,paypal_order_id,expected_amount,currency_code`.
Results are classified as matched, amount mismatch, currency mismatch, needs
review (duplicates or no lookup), order not found, or lookup error.

**Tests need no credentials:** `python -m pytest -q`.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Serves the web interface (redirects to `/login` when signed out) |
| GET | `/login` | Serves the sign-in / first-time setup page |
| GET | `/health` | Checks API availability |
| GET | `/api/auth/status` | Reports whether setup is required and whether the caller is signed in |
| POST | `/api/auth/setup` | Creates the first owner account (only while none exists) and signs in |
| POST | `/api/auth/login` | Signs in with a username and password |
| GET | `/api/auth/users` | Lists accounts (owner only) |
| GET | `/api/auth/audit` | Recent security audit events with the acting owner (owner only; `limit` 1–500) |
| POST | `/api/auth/users` | Creates an employee account (owner only, CSRF token required) |
| POST | `/api/auth/users/{id}/deactivate` | Deactivates an employee and revokes their sessions (owner only) |
| POST | `/api/auth/users/{id}/reactivate` | Reactivates an employee (owner only) |
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
  address per 5 minutes, held in memory). There are two roles. The single owner is created
  during first-time setup; there is no public registration. The owner can add,
  deactivate, and reactivate employee accounts (the **Team accounts** panel).
  Employees can investigate, reconcile, and view history and analytics but
  cannot manage accounts; owners cannot be created, changed, or deactivated
  through the API. Account creation, deactivation, and reactivation are
  audited (the audit table does not yet record which owner acted). There is
  no password reset or multi-factor authentication.
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

## Runtime data, backup and recovery

Set `PAYPILOT_DATA_DIR` to keep the database, backups, logs, exports and
recovery copies outside the source repository. See
[docs/DATA-LAYOUT.md](docs/DATA-LAYOUT.md) for the layout, `scripts/backup_db.py`
and `scripts/restore_db.py`.
