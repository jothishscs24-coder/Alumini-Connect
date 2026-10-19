# AlumniConnect — Smart Alumni Networking Platform

A full-stack alumni networking application upgraded from the supplied demo. It uses a single-page web UI, FastAPI REST API, SQLAlchemy data models, PostgreSQL persistence, JWT authentication, role-based access, and semantic profile recommendations powered by sentence-transformer embeddings.

## What is implemented

- Student/alumni registration and login; administrator accounts are bootstrapped only from environment variables.
- Password hashing with PBKDF2-HMAC-SHA256 and expiring signed JWT access tokens.
- Profile creation/editing and authenticated directory search/filtering.
- Semantic recommendations based on profile embeddings, with shared-skill/career/department explanations. The default embedding model is `sentence-transformers/all-MiniLM-L6-v2`.
- Connection requests and accept/decline workflows.
- Student-to-alumni mentorship requests and accept/decline workflows.
- Community posts with persistent helpful/bookmark reactions and comments, connection-only private messaging, read state, and notifications.
- Admin account listing, alumni verification, and account activation/deactivation.
- OpenAPI documentation and health/readiness endpoints.
- The original prototype is available at `/demo` in development for UI comparison; the production app hides the prototype and serves the API-backed application at `/`.
- Directory search is API-backed and paginated; connection and mentorship requests show the other participant's public profile details.

## Requirements

- Python 3.11 or newer (Docker uses Python 3.12).
- PostgreSQL 14+ for the intended deployment, or Docker Desktop to run the bundled PostgreSQL service.
- Internet access on the first recommendation request if the sentence-transformer model is not already cached. The model is downloaded by `sentence-transformers` on first use. If model loading fails, the API falls back to transparent lexical similarity instead of failing the request and retries loading after a 60-second cooldown. Monitor logs/model availability to know whether true embedding-based matching is active.

## Local matching showcase

To populate a development SQLite database with synthetic profiles for a student-to-alumni matching demo, start the app locally and run:

```powershell
python -m backend.seed_demo
```

The command is explicit and repeatable; the app never seeds these accounts on startup. It refuses `APP_ENV=staging` or `production` and refuses non-SQLite `DATABASE_URL` values. All names, workplaces, and profiles are fabricated. Sign in as `demo.student@example.com` with `DemoStudent!2026`, then open **AI recommendations**. The four alumni accounts use `DemoAlumni!2026` and addresses under `demo.alumni.*@example.com`. These credentials are for local showcase data only and must never be reused in a deployment.

## Option A — Run with Docker Compose (recommended)

1. Install Docker Desktop and start it.
2. Copy `.env.example` to `.env`.
3. Set strong, unique values for `POSTGRES_PASSWORD` and `JWT_SECRET` (at least 32 characters). Set `APP_ENV=production` for deployment. Keep `ENABLE_LEGACY_DEMO_STATE=false` in shared environments. Keep the PostgreSQL password URL-safe (letters and numbers are easiest). Optionally set `ADMIN_EMAIL` and `ADMIN_PASSWORD` to create the first administrator on startup.
4. From this directory, run:

   ```bash
   docker compose up --build
   ```

5. Open the application:
   - App: http://localhost:8000
   - Health: http://localhost:8000/api/health
   - Database readiness: http://localhost:8000/api/health/ready
   - API docs are disabled by default in production. For a local development run, set `APP_ENV=development` and `ENABLE_API_DOCS=true` before starting.

Stop it with `docker compose down`. Data persists in the `alumni_pgdata` volume. **Destructive reset:** `docker compose down -v` removes the database volume and all stored data.

## Deploy to Render

The root `render.yaml` Blueprint creates the Docker web service, PostgreSQL database, and private Redis-compatible Key Value instance. Push the repository to GitHub, then in Render choose **New > Blueprint** and select the repository. The Blueprint generates `JWT_SECRET` and wires the database and Redis connection URLs automatically. To bootstrap the first administrator, set both `ADMIN_EMAIL` and `ADMIN_PASSWORD` in the web service's environment settings.

This Blueprint uses Render's free plans to make initial deployment straightforward. Free web services can sleep when idle, free Key Value data is not persistent, and free PostgreSQL databases expire after their current free-retention period; use paid plans and backups for durable production data. The sentence-transformer model may also require more memory than a free web instance provides; if model loading fails, recommendations fall back to lexical similarity. The `/api/health/ready` endpoint checks the database and shared rate-limit store.

## Option B — Use your own PostgreSQL server

Create a database and dedicated user using your PostgreSQL administrator account, for example:

```sql
CREATE USER alumni_app WITH PASSWORD 'replace_with_a_strong_password';
CREATE DATABASE alumniconnect OWNER alumni_app;
GRANT ALL PRIVILEGES ON DATABASE alumniconnect TO alumni_app;
```

Set these environment variables in your shell before starting the app (adjust the host/port and credentials):

```text
DATABASE_URL=postgresql+psycopg://alumni_app:replace_with_a_strong_password@localhost:5432/alumniconnect
JWT_SECRET=replace_with_a_random_secret_of_at_least_32_characters
ACCESS_TOKEN_MINUTES=60
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

For the first administrator, also set `ADMIN_EMAIL` and `ADMIN_PASSWORD`. For staging/production, configure `RATE_LIMIT_REDIS_URL` to a reachable shared Redis instance; startup fails closed without it. Production Docker startup runs `alembic upgrade head` before Uvicorn. Local development/test can still create tables automatically. Back up your database before applying migrations. For a database created by an earlier version without `alembic_version`, do not run the initial migration blindly: back it up, compare its schema with the initial migration, and intentionally baseline it with `alembic stamp head` only after verifying that all expected tables and columns already exist.

Install and start the app:

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://alumni_app:replace_with_a_strong_password@localhost:5432/alumniconnect"
$env:JWT_SECRET = "replace_with_a_random_secret_of_at_least_32_characters"
uvicorn backend.app.main:app --reload
```

**macOS/Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL='postgresql+psycopg://alumni_app:replace_with_a_strong_password@localhost:5432/alumniconnect'
export JWT_SECRET='replace_with_a_random_secret_of_at_least_32_characters'
uvicorn backend.app.main:app --reload
```

For a quick local-only review without PostgreSQL, omit `DATABASE_URL`; the app uses SQLite at `backend/data/alumniconnect.db`. If `JWT_SECRET` is not set in development, a random process-local secret is generated, so tokens become invalid after restart. Production startup fails if the secret is missing, a known placeholder, or shorter than 32 characters. The legacy mutable `/api/state` demo endpoints are disabled by default; enable `ENABLE_LEGACY_DEMO_STATE=true` only for isolated local demos. Do not expose that compatibility API to multiple users or the public internet.

## Create the first admin

Admin registration is intentionally not exposed in the public registration form. Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before first startup. If the email already exists, startup does not reset that account's password. Use a unique password and store secrets outside source control.

## Main API routes

All routes below except health/root require a bearer access token where noted. Use `/docs` for request schemas and interactive testing.

| Area | Routes |
|---|---|
| Health | `GET /api/health`, `GET /api/health/ready` |
| Authentication | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` |
| Profiles | `GET /api/profiles`, `GET /api/profiles/{id}`, `PUT /api/profiles/me` |
| AI matching | `GET /api/recommendations?limit=10` |
| Connections | `POST /api/connections`, `GET /api/connections`, `PATCH /api/connections/{id}` |
| Mentorship | `POST /api/mentorship`, `GET /api/mentorship`, `PATCH /api/mentorship/{id}` |
| Community | `GET /api/posts`, `POST /api/posts`, `PUT /api/posts/{id}/reactions/{kind}` (liked or bookmarked), `GET/POST /api/posts/{id}/comments` |
| Messages | `POST /api/messages`, `GET /api/messages/{other_user_id}` |
| Notifications | `GET /api/notifications`, `PATCH /api/notifications/{id}/read` |
| Admin | `GET /api/admin/users`, `PATCH /api/admin/users/{id}/verify`, `PATCH /api/admin/users/{id}/active` |

## Run tests

```bash
pip install -r backend/requirements-dev.txt
python -m pytest -q backend/tests
```

The automated API suite uses a disposable SQLite database so it does not require PostgreSQL, Redis, or a model download. It checks registration/login, profile editing, directory search and pagination, networking permissions, community reactions/comments, notifications, admin actions, and messaging access. To run the real-browser flow, install Chromium with Playwright and set `RUN_BROWSER_E2E=1`; CI runs it automatically. CI also runs a separate PostgreSQL/Redis integration job that migrates PostgreSQL and checks the shared Redis rate limiter. The embedding model itself is exercised only when it is installed and available; API tests deliberately allow the lexical fallback.

### Recommendation quality evaluation

Run the deterministic synthetic-corpus baseline with `python -m backend.evaluation.recommender_eval --mode lexical --k 3`. It reports macro Recall@K, MRR@K, and graded NDCG@K; CI enforces a broad lexical-baseline floor and uploads the JSON report. To require the configured sentence-transformer and score its actual embeddings, run `python -m backend.evaluation.recommender_eval --mode semantic --k 3`; the command fails if the model cannot produce every vector. GitHub Actions can run this model evaluation manually with the `run_semantic_evaluation` input enabled. The included labels and profiles are synthetic smoke data, not a substitute for evaluation on representative, consented profiles before production claims.

## Security and production notes

- Public registration permits only `student` and `alumni`; admin accounts are bootstrapped out-of-band.
- Alumni verification is controlled by administrators. Only verified alumni have their email exposed in directory/profile responses.
- Message sending and reading are restricted to accepted connections; only students can initiate mentorship requests, and only the target alumni can respond.
- Use HTTPS, strong secrets, PostgreSQL backups, monitoring, and a managed process/container runtime for deployment.
- High-risk API routes have Redis-backed shared rate limiting in staging/production and a process-local fallback only in development/tests. Redis availability is required at deployment startup.
- Production responses include a CSP with `script-src 'self'`; the main app's JavaScript is served externally and has no inline event handlers. Inline styles remain allowed. The prototype page is disabled in production.
- Account email verification and password recovery are intentionally deferred by project decision and are not implemented. They are outside this finalized local showcase scope; production launch still requires an explicit product/security decision plus audit logging, operational monitoring, and privacy/data-retention policies. This repo is a functional application baseline, not a security audit or a guarantee of production compliance.


## Security audit and remediation status

- [`RUTHLESS_AUDIT_REPORT.md`](RUTHLESS_AUDIT_REPORT.md): historical first-pass findings.
- [`RUTHLESS_TEST_REPORT.md`](RUTHLESS_TEST_REPORT.md): historical adversarial run and original failure evidence.
- [`SECURITY_REMEDIATION_PROGRESS.md`](SECURITY_REMEDIATION_PROGRESS.md): fixes completed and release blockers still open.
- [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md): latest reproducible local test results and environment limitations.

**Do not deploy publicly yet.** Run and review the new PostgreSQL/Redis CI job and browser CI, validate model ranking quality, decide whether to implement the intentionally deferred account email workflows or accept their absence, and complete operational controls before launch.
