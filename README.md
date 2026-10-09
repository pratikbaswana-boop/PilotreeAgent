# Logistics Enquiry Triage Tool

A reviewer workspace for reading logistics enquiries, running AI analysis, editing and approving results, and explicitly sending to configured Slack, Linear, or Sheets destinations.

`DESIGN.md` contains the system design. `DECISIONS.md` records implementation choices.

## Run locally

Prerequisites: Python 3.12+, Node 20.19+ (Node 22.12+ recommended), PostgreSQL 16+.

```bash
docker compose up -d postgres
cd backend
uv venv
uv pip install -e '.[dev]'
cp ../.env.example .env
# Configure DATABASE_URL, DATABASE_SESSION_POOL_URL, OIDC and GEMINI_API_KEY.
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

In a second terminal, start the worker from `backend/`:

```bash
uv run python -m app.jobs.worker
```

The worker registers `analyse`, `send_action`, and `resume_review`. It initializes the LangGraph checkpoint schema on the session connection, recovers legacy pending action rows, heartbeats job leases, and sweeps stuck sends. The database role needs schema creation privileges for the first checkpoint setup.

In a third terminal:

```bash
cd frontend
npm ci
cp .env.example .env
# Set VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID.
npm run dev
```

Open the Vite URL. Register its exact `/auth/callback` URL in your OIDC client. The SPA uses authorization code with PKCE and silent token renewal; the API verifies the resulting access token. User roles are stored in the backend `users` table; newly provisioned accounts default to `viewer`. A reviewer/admin role is needed for mutations.

The Vite development proxy sends `/api` to `http://127.0.0.1:8000`. In production, serve `frontend/dist` with SPA route fallback and reverse-proxy `/api` to FastAPI. Configure your host's CSP for the API and OIDC origins. This change does not deploy the application.

## Implemented flow

- Enquiries master-detail view, URL filters, virtualized list, mobile detail route, theme toggle, command search, and OIDC session guard.
- Analysis jobs run the safety graph and ProviderRouter, checkpoint the interrupt, persist result/usage/verdicts, and emit phase/result events.
- Editable result form with validation, optimistic saves, explicit conflict reload/merge, five safety states, manual triage, review approval/rejection and admin override.
- Approval and sending remain separate user actions. Destination selection previews the payload; resend and unknown-outcome retry require reasons.
- Outbox jobs commit `sending` before network I/O, record attempts, back off confirmed retryable failures, and reconcile ambiguous results. Unknown sends are never silently replayed.
- Fetch-based SSE reconnects with a fresh access token and Last-Event-ID; query polling covers disconnected periods. Loading, empty, failure, offline, and expired-session states are included.

Configure destinations through the existing `/admin/tools/{key}` API. Tool configurations must match the schemas in `backend/app/tools/`. Real OIDC, Gemini, and destination credentials are required for live end-to-end use; none are embedded in the SPA.

## Verify

Backend tests use a disposable local `triage_test` database and the test credentials in `backend/tests/conftest.py`:

```bash
cd backend
uv run pytest -q
```

Browser tests mock the API and external services; worker integration tests use real PostgreSQL and controlled provider/tool fakes, including persistent checkpoint recovery:

```bash
cd frontend
npm run build
npx playwright install chromium
npm run test:e2e
```

Regenerate the frontend API schema after changing FastAPI routes:

```bash
cd backend
uv run python scripts/export_openapi.py
cd ../frontend
npm run generate:api
```

## Operational limits

- Live external delivery and your organisation's OIDC refresh behavior require validation with your configured accounts. Automated tests do not send real messages.
- Existing admin credential encryption/secret-manager support remains unfinished; protect admin endpoints and tool configuration storage. The UI does not expose credentials.
- PII reveal with an audited reveal endpoint, aggregate navigation counts, and full administrator configuration forms are not implemented in this frontend. The original enquiry remains visible according to existing API authorization.
- Review persistence stays atomic in the API; a durable job completes the graph checkpoint afterward (see decision 9).

### Optional local development sign-in

For localhost testing without an organisation identity provider, set `LOCAL_DEVELOPMENT_AUTH=true` and a randomly generated `LOCAL_DEVELOPMENT_SECRET` (at least 32 characters) in `backend/.env`, plus `VITE_LOCAL_DEVELOPMENT_AUTH=true` in `frontend/.env`. Bind both servers to loopback. The login screen then offers **Enter local workspace**, issuing an eight-hour signed reviewer session. The endpoint rejects non-loopback callers and unapproved origins; the frontend option is disabled in production builds. The default remains OIDC.

Use **New enquiry** to paste a message, then **Analyse with AI**. The configured Gemini model must be available to your API key; `gemini-3.5-flash-lite` was verified with a real structured call in this local setup. Availability and free-tier quotas are controlled by Google.
