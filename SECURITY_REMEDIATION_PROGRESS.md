# AlumniConnect Remediation Progress

Last updated: 2026-10-16

## Completed in this pass

- [x] Directory search now uses bounded server-side query parameters and paginated results in the main UI.
- [x] Connection and mentorship request lists include the other participant's public name, role, and relevant job/company details; inactive or unverified alumni are not exposed by this summary.
- [x] Community helpful/bookmark reactions are persisted per user and idempotent; comment create/list endpoints validate input and notify post authors.
- [x] Added an Alembic migration for post reactions and comments.
- [x] Main app JavaScript is external, the main app contains no inline script or event-handler attributes, and production applies `script-src 'self'` CSP directives.
- [x] Production hides the legacy prototype route and its static HTML copy.
- [x] Browser E2E now covers registration, hostile profile names in messaging, and hostile community comment rendering.
- [x] CI has a PostgreSQL/Redis integration job that applies production migrations and checks persistence, readiness, and shared throttling.

## Local validation on 2026-10-16

- `python -m pytest -q backend/tests/test_api.py`: **57 passed** (one Starlette/httpx deprecation warning).
- `RUN_BROWSER_E2E=1 python -m pytest -q backend/tests/test_browser_e2e.py`: **1 passed** using Chromium installed into a temporary project-local directory.
- `python -m compileall -q backend migrations`: passed.
- Node syntax checks for `frontend/app.js` and the development prototype script: passed.
- Production main-page checks for no inline script/handlers and external app script: passed.
- Alembic SQLite upgrade, metadata check, and downgrade: passed; the API migration regression also passed.
- GitHub Actions and Docker Compose YAML parse: passed.

## Still open / environment-dependent

1. Account email verification and password recovery are intentionally deferred by project decision and are not implemented. Revisit their scope before launch, or explicitly accept operating without them.
2. The new PostgreSQL/Redis integration job is configured but has not run in this local environment; Docker and Redis services are unavailable here.
3. The Docker Compose app image and startup path still need a Docker-capable run.
4. Real sentence-transformer inference and ranking quality still need validation with the target model and representative profiles.
5. Existing pre-Alembic databases still require backup, schema comparison, and intentional baselining; do not run the initial migration blindly against an unversioned database.
6. Audit logging, monitoring/alerting, privacy and retention policy, backup restoration drills, and incident response procedures remain unfinished.
7. Review results from the CI dependency audit before release.

## Release rule

Do not publicly deploy until the remaining release blockers are completed and validated or explicitly assessed by the project owner. Local checks cover only the tested paths and do not certify the application as secure.
