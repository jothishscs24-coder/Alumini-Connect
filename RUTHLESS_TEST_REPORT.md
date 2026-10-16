> **Remediation status update (2026-10-15):** This is a historical report from before the latest remediation pass. See [`SECURITY_REMEDIATION_PROGRESS.md`](SECURITY_REMEDIATION_PROGRESS.md) and [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) for the current state. The current local suite reports 53 passed and 1 browser test skipped because Chromium is unavailable; PostgreSQL, Redis, real model inference, and browser CI remain unverified locally. Do not interpret this historical report's failure count as the current test-suite result, and do not deploy publicly yet.

# AlumniConnect — Ruthless Top-to-Bottom Test Report

**Test date:** 2026-10-15  
**Artifact tested:** `AlumniConnect_Security_Fixed.zip` (the security-fixed bundle)  
**Scope:** FastAPI backend, authentication/authorization, profile directory, recommendations, connections, mentorship, posts, messaging, notifications, admin routes, legacy demo endpoints, frontend inline JavaScript, app configuration, dependency/runtime readiness.  
**Verdict: NOT READY FOR PUBLIC/PRODUCTION DEPLOYMENT.** The previous security remediations hold for the cases tested, but adversarial tests found additional authorization/product-integrity issues and weak input bounds.

## Executive result

| Test group | Result | Details |
|---|---|---|
| Existing automated suite | PASS | `python -m pytest -q backend/tests` — **14 passed** |
| Python compilation | PASS | `python -m compileall -q backend` |
| Frontend JS syntax | PASS | `node --check` on inline scripts in `index.html` and `demo.html` |
| Fixed XSS regression guard | PASS, limited | Static test confirms chat names are no longer interpolated into an inline JS handler; no real browser available to run DOM/XSS browser tests |
| Legacy shared-state guard | PASS | State endpoints return 404 when the feature flag is off; original tests enable it for demo compatibility |
| Production JWT placeholder guard | PASS for exact placeholder | Startup rejects the exact sample placeholder and missing secret when `APP_ENV=production` |
| Adversarial test suite | **FAIL — 6 failed, 4 passed** | See AC-07 through AC-09 below; failing tests deliberately assert expected safety/product rules |
| Environment config probes | **FAIL — 2 unsafe configs accepted** | `APP_ENV=prod` with no secret starts; 40-character repeated `a` secret starts in production |
| Rate-limit probe | **FAIL / absent control** | 20 consecutive bad login attempts all returned 401; no throttling/429 observed |
| Security response headers | **FAIL / absent** | No CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, or HSTS header observed on `/api/health` |
| API docs | Exposed | `/docs` and `/openapi.json` return 200, including under test configuration; production behavior not separately deployed/probed |
| PostgreSQL/Docker | NOT TESTED | Docker executable is unavailable and `psycopg` is not installed in the test runtime |
| Real transformer inference | NOT TESTED | `sentence_transformers` and model weights are not installed/cached in this runtime |
| Browser interaction | NOT TESTED | Playwright is installed, but Chromium browser executable is missing |
| Dependency audit | NOT RUN | `pip-audit` and Bandit are not installed. `pip check` reports a shared-environment conflict: moviepy 2.2.1 requires Pillow `<12`, installed Pillow is 12.3.0; this does not prove the project requirements themselves cause that conflict. |

## Confirmed findings

### AC-07 — Recommendations include student accounts

**Severity: Medium — core product correctness.**  
**Evidence:** Created two student accounts and called `GET /api/recommendations` as one student. The response included the other student with `role: "student"`.  
**Impact:** The feature is described as recommending alumni/mentors, but it can recommend students instead. This degrades the core experience and can make the dashboard misleading.  
**Fix:** Filter recommendations to active alumni accounts (and, if the product promise is verified alumni, require `is_verified=true`). Add tests for role and verification filters.

### AC-08 — Unverified alumni can receive connection and mentorship requests

**Severity: Medium to High — trust/safety and identity integrity.**  
**Evidence:** A newly self-registered, unverified alumni account received `201 Created` for both `POST /api/connections` and `POST /api/mentorship`.  
**Impact:** The UI advertises verified alumni, and the admin workflow supports verification, but verification is not enforced at these action boundaries. A user can claim to be an alumnus and receive outreach before review. Unverified accounts also appear in recommendations (AC-07).  
**Fix:** Enforce the verified-alumni policy in recommendation, connection, and mentorship endpoints server-side. If unverified alumni are intentionally allowed to connect, document that policy explicitly and do not label them as vetted mentors.

### AC-09 — Skill entries and directory filters lack per-value length bounds

**Severity: Medium — resource exhaustion / data quality.**  
**Evidence:** Registration accepted a single 20,000-character skill value with `201 Created`; `/api/profiles?department=` accepted a 5,000-character filter with `200 OK`. `Field(max_length=40)` on `list[str]` limits the number of entries, not the length of each string. The `department` and `skill` query parameters have no declared maximum length.  
**Impact:** Large profile values can inflate database storage, recommendation text, embedding inference cost, and response/query work. Very large query values also create unnecessary database work.  
**Fix:** Use bounded item types (e.g. `list[Annotated[str, Field(max_length=80)]]`) and bounded `Query(max_length=...)` for all search filters. Consider a request-body size limit at the reverse proxy/server too.

### AC-10 — Production mode detection can be bypassed with `APP_ENV=prod`

**Severity: High configuration hardening gap.**  
**Evidence:** A subprocess import with `APP_ENV=prod` and no `JWT_SECRET` succeeded. The fail-closed check only triggers for the exact string `production`.  
**Impact:** Operators using the common `prod` spelling can accidentally start with a randomly generated process-local key rather than a configured stable secret. Existing tokens then become invalid on restart, and the intended deployment guard is bypassed.  
**Fix:** Normalize accepted environment names (`prod`, `production`) or, preferably, require a secret for every environment other than explicit `development`/`test`.

### AC-11 — JWT secret length is checked, but low-entropy values pass

**Severity: High if operators choose a predictable secret.**  
**Evidence:** `APP_ENV=production` with `JWT_SECRET` set to 40 repeated `a` characters successfully imported the application.  
**Impact:** A length-only rule does not establish a strong, unique signing key.  
**Fix:** Generate secrets with a cryptographically secure random generator and validate against known placeholders; document a secure generation command. Avoid trying to infer entropy from length alone. A deployment secret manager is preferred.

### AC-12 — No login/register rate limiting

**Severity: High for an internet-facing service.**  
**Evidence:** Twenty consecutive invalid login requests all returned 401, with no throttling response. Source review found no rate-limit middleware/dependency.  
**Impact:** Credential guessing, account enumeration attempts, and automated account creation are not throttled at the application layer. Password hashing also makes each registration/login attempt comparatively expensive.  
**Fix:** Add shared-store rate limiting at the proxy or app layer, keyed by IP and account identifier, with stricter limits for login/register and appropriate `429` responses. Add tests and account lockout/abuse telemetry that avoid permanent easy denial of service.

### AC-13 — Security headers are absent

**Severity: Medium.**  
**Evidence:** The `/api/health` response had none of the checked headers: Content-Security-Policy, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, Strict-Transport-Security.  
**Impact:** The app misses defense-in-depth protections against framing, MIME sniffing, information leakage, and script injection. HSTS is only appropriate once HTTPS is correctly terminated.  
**Fix:** Add appropriate security headers (ideally at a trusted reverse proxy); create a CSP compatible with the frontend or refactor inline scripts/styles so a strict CSP can be used. Enable HSTS only in HTTPS deployment.

### AC-14 — Public API documentation remains enabled

**Severity: Low to Medium, deployment-policy dependent.**  
**Evidence:** `/docs` and `/openapi.json` returned 200.  
**Impact:** This exposes the complete route/schema surface to unauthenticated visitors. It is not by itself an authorization bypass, but many production systems disable or protect interactive docs.  
**Fix:** Make docs/OpenAPI exposure configurable by environment and disable it in production unless deliberately required.

### AC-15 — Frontend browser-level regression testing could not run

**Severity: Release verification gap, not a proven code vulnerability.**  
**Evidence:** Playwright Python is installed, but the Chromium executable is absent. JavaScript syntax checks passed, but syntax checks do not execute DOM rendering, event handlers, accessibility flows, or stored-XSS payloads in a browser.  
**Fix:** Add browser-based CI with a pinned browser, test registration/login, profile update, discover/recommendations, connection accept, messaging, notifications, admin actions, mobile layout, and hostile names/post/message payloads.

## Checks that passed

- Registration, login, `/api/auth/me`, invalid-token handling, duplicate email conflict.
- Anonymous access to authenticated directory was denied.
- Admin route rejected non-admin and allowed admin verification.
- Inactive users were rejected when using an existing token.
- Non-recipient could not accept a connection request.
- Registration could not set `role=admin`.
- Profile endpoint did not expose the internal embedding vector.
- Message body length limit rejected a body over 5,000 characters.
- Previously remediated shared-state feature flag and exact placeholder secret checks passed in the existing suite.
- Chat-name rendering no longer interpolates the name into an inline JavaScript handler; frontend scripts parse successfully.

## Commands and probes executed

```bash
python -m pytest -q backend/tests
python -m compileall -q backend
node --check /tmp/index_script_0.js
node --check /tmp/demo_script_0.js
python -m pip check
```

Additional isolated adversarial suite: **10 tests, 6 failed and 4 passed**. The six failures were the two recommendation policy cases, unverified alumni connection, unverified alumni mentorship, oversized skill acceptance, and oversized directory filter acceptance. Configuration subprocess probes separately demonstrated the `APP_ENV=prod` and low-entropy secret issues.

## Release blockers / next remediation order

1. Fix AC-10 and AC-11 (fail-closed environment/JWT secret policy).
2. Decide and enforce verified-alumni rules for recommendations, connections, and mentorship (AC-07/08).
3. Bound skill strings and all search query parameters; add request size limits (AC-09).
4. Add login/register rate limiting and security headers (AC-12/13).
5. Decide production OpenAPI policy (AC-14).
6. Add browser CI, PostgreSQL integration CI, Docker smoke test, and real model inference/ranking assertions.
7. Add schema migrations, account recovery/email verification, logging/monitoring, and run a dependency vulnerability scan before public release.

**Final verdict:** the prior critical patches improved the project, but the passing 14-test suite is not sufficient evidence of production safety. Do not deploy publicly until the high-severity configuration and abuse-control gaps are addressed and the intended PostgreSQL/AI runtime is tested.
