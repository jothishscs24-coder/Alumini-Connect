> **Remediation status update (2026-10-15):** This is a historical report from before the latest remediation pass. See [`SECURITY_REMEDIATION_PROGRESS.md`](SECURITY_REMEDIATION_PROGRESS.md) and [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) for the current state. The current local suite reports 53 passed and 1 browser test skipped because Chromium is unavailable; PostgreSQL, Redis, real model inference, and browser CI remain unverified locally. Do not interpret this historical report's failure count as the current test-suite result, and do not deploy publicly yet.

# AlumniConnect Ruthless Test & Security Audit

**Audit date:** 2026-10-15  
**Scope:** source in this archive (`backend/app/main.py`, API tests, `frontend/index.html`, Docker Compose and requirements).  
**Verdict:** **NOT production-ready.** The normal API regression suite passes, but adversarial review found a stored-XSS path and unauthenticated global-state mutation. The intended PostgreSQL/Docker deployment and real embedding-model inference could not be verified in this environment.

## Executive summary

| Area | Result | Evidence / limitation |
|---|---|---|
| Existing automated API tests | PASS | `python -m pytest -q backend/tests` — 10 passed in 2.15s |
| Python syntax/bytecode compilation | PASS | `python -m compileall -q backend` |
| Frontend JavaScript syntax | PASS | Extracted inline script and ran `node --check` |
| Auth happy path / invalid token | PASS | Registration and `/api/auth/me` returned 201/200; malformed bearer token returned 401 |
| Authenticated directory gate | PASS | Anonymous `/api/profiles` returned 401 |
| Unverified alumni email privacy in directory | PASS | Directory response did not expose email for an unverified alumni profile |
| Public legacy state endpoint | **FAIL — HIGH** | Anonymous GET and PUT both returned 200; PUT changed global shared demo state |
| Stored XSS in Messages connection list | **FAIL — HIGH** | Untrusted display name is interpolated into an inline JavaScript handler after HTML escaping; entities decode before handler JavaScript executes |
| Default JWT secret | **FAIL — HIGH for deployment** | App silently uses a known development signing secret if `JWT_SECRET` is unset |
| PostgreSQL/Docker runtime | NOT TESTED | Docker executable is unavailable in this environment; no PostgreSQL server was available |
| Actual embedding inference | NOT TESTED | `sentence_transformers` is not installed in this test runtime; tests can use lexical fallback |
| Dependency audit | NOT RUN | `pip-audit`/Bandit/Ruff are unavailable; `pip check` reports an unrelated pre-existing environment conflict: `moviepy` requires Pillow `<12`, installed Pillow is `12.3.0` |

## Findings

### AC-01 — Stored cross-site scripting via profile name

**Severity: High.**  
**Location:** `frontend/index.html`, `messagesPage()` connection button template.

The template builds an inline handler similar to:

```js
onclick="openChat(ID, '${esc(p.full_name).replace(/'/g, "\\'")}')"
```

The `esc()` helper converts apostrophes to the HTML entity `&#39;`. The subsequent replacement looks for literal apostrophes, so it does not escape the entity. When the browser parses the attribute, `&#39;` becomes an apostrophe before the inline handler is compiled. A malicious profile name such as `');alert(document.domain);//` can break out of the intended JavaScript string. A user who can get accepted as a connection can potentially trigger this when another user opens Messages. The UI also stores bearer tokens in `localStorage`, increasing impact if script execution occurs.

**Required fix:** do not put user-controlled values into inline JavaScript handlers. Bind click handlers with `addEventListener`, use DOM nodes and `textContent`, or use a safe serialization strategy that is correct for the HTML *and* JavaScript contexts. Add a regression test with quote/markup payloads and verify they render as inert text.

### AC-02 — Anonymous global demo-state read/write

**Severity: High if deployed; medium for a strictly local disposable demo.**  
**Location:** `backend/app/main.py`, `GET /api/state` and `PUT /api/state`.

Both endpoints have no authentication dependency. A direct probe confirmed anonymous GET returned `200`, anonymous PUT returned `200`, and a changed `profile.name` persisted for the next request. The object is a single shared state row, so any visitor can read/overwrite the same global state. This can deface the legacy demo and may expose data if non-demo content is ever stored there.

**Required fix:** remove these endpoints from public deployment or put them behind an explicit local-demo feature flag that is disabled by default in shared environments. Do not treat a JSON schema as authorization. Add tests that anonymous reads/writes are denied when the demo feature is disabled.

### AC-03 — Known fallback JWT secret accepted silently

**Severity: High for any reachable shared deployment.**  
**Location:** `backend/app/main.py`, `JWT_SECRET` configuration.

If the environment variable is omitted, tokens are signed with a hard-coded public development value. Anyone who knows that value can forge HS256 tokens, including tokens with an admin role claim; the database lookup still checks the user id and role from the database for authorization, but token forgery remains a serious authentication failure and could be combined with account/id knowledge or future code paths.

**Required fix:** fail application startup outside explicit development/test mode if the secret is absent, unchanged from the known default, or too short. Add a configuration test. Rotate the secret before deployment.

### AC-04 — Embedding failure is cached for the process lifetime

**Severity: Medium (AI feature availability/reliability).**  
**Location:** `backend/app/main.py`, `_embed()`.

After the first model-load exception, `_embedding_load_error` remains set and prevents another load attempt for the life of the process. A transient network failure or unavailable model at first request therefore leaves the service using lexical fallback until restart. The response indicates whether embeddings were used, which is good, but there is no retry/backoff or operator-visible health status.

**Required fix:** implement bounded retry/backoff or an explicit admin reload path, expose model readiness in health output, and test both successful model loading and failure recovery.

### AC-05 — PostgreSQL schema lifecycle lacks migrations

**Severity: Medium (release/operations).**  
**Location:** startup calls `Base.metadata.create_all()`.

This creates missing tables but does not safely migrate existing schemas when columns or constraints change. The README notes this limitation. It is acceptable for a prototype, not for a maintained production database.

**Required fix:** add Alembic migrations, run them as a controlled deployment step, and test upgrades from a prior schema.

### AC-06 — Real AI and production runtime remain unverified

**Severity: Release blocker for claiming a fully verified deployment.**

The API tests permit lexical fallback; they do not prove that the transformer model downloads, loads, returns embeddings, persists vectors, or produces semantically sensible ranking. Docker and PostgreSQL could not be started in this environment. The app image installs `sentence-transformers`, which may significantly increase image size and first-start time, and model download requires network/cache availability.

**Required fix:** run CI with PostgreSQL service and a cached/pinned embedding model; assert `embedding_used=true` for a controlled semantic ranking fixture; test container health, restart persistence, and a clean first boot.

## Commands executed

```bash
python -m pytest -q backend/tests
python -m compileall -q backend
node --check /tmp/alumniconnect_frontend.js
python -m pip check
```

Results: 10 tests passed; compilation and JS syntax checks passed. `pip check` failed because the shared runtime has `moviepy 2.2.1` installed alongside `Pillow 12.3.0`, while moviepy declares `Pillow<12.0`; this does not establish that the app's pinned requirements are inconsistent. Docker could not be invoked because the `docker` executable is absent.

## Release gate (must pass before public launch)

- [ ] Fix AC-01 stored XSS and add regression coverage.
- [ ] Disable/protect AC-02 legacy state endpoints in shared environments.
- [ ] Fail closed on missing/default JWT secret (AC-03).
- [ ] Run PostgreSQL integration tests and Docker smoke test.
- [ ] Verify actual embedding inference and ranking; no silent fallback in the production smoke test.
- [ ] Add migrations, rate limiting, account recovery/email verification, audit logging, and security headers/CSP.
- [ ] Run dependency vulnerability scanning and a dedicated authorization/IDOR test suite.


## Remediation pass (2026-10-15)

Applied fixes in the updated project bundle:

- **AC-01 Stored XSS:** removed the inline `openChat(id, 'name')` JavaScript interpolation. Connection buttons now use HTML data attributes and registered event listeners; names are not parsed as JavaScript source.
- **AC-02 Shared demo state:** `/api/state` GET/PUT now return 404 unless `ENABLE_LEGACY_DEMO_STATE=true` is explicitly configured. The default is disabled.
- **AC-03 JWT default:** production startup fails closed if `JWT_SECRET` is missing, a known placeholder, or shorter than 32 characters. Development without a configured secret gets a random process-local secret rather than a known static one.
- **AC-04 Embedding retry:** model-load/encoding failures now trigger a 60-second retry cooldown rather than permanently disabling semantic embeddings for the life of the process. The API still uses lexical fallback during an outage.
- Added regression tests for the above controls and updated `.env.example`/README.

### Remediation verification

The test suite is run after remediation. This container does not have Docker available, and the sentence-transformers model is not installed/cached here, so PostgreSQL-in-Compose and actual model inference remain unverified in this environment. Rate limiting, account recovery/email verification, schema migrations, security scanning, and operational monitoring remain production-hardening work; this remediation is not a claim of full production certification.
