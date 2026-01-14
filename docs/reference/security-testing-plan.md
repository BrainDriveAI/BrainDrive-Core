# Security Testing Plan - BrainDrive

This plan verifies the security roadmap implementation and guards against regressions in BrainDrive-Core. It layers automated checks, targeted manual testing, and repeatable evidence capture so results are auditable.

## 1) Scope

In scope:
- Backend API (`backend/app`, `backend/main.py`, `backend/scripts`)
- Frontend client (`frontend/src`, auth/session handling)
- Plugin lifecycle and file handling (`backend/app/routers/plugins.py`, `backend/plugins`)
- Internal service endpoints (`backend/app/api/v1/internal`)
- Configuration defaults and environment enforcement (`backend/app/core/config.py`)

Out of scope (unless explicitly requested):
- Third-party hosted services
- External SaaS integrations beyond auth/token handling

## 2) Environments and Data

Environments:
- Local dev (baseline behavior)
- Staging or prod-like (required for final sign-off)

Test data:
- Admin user, standard user, suspended user (if supported)
- Service tokens for internal endpoints (plugin/runtime/worker)
- Seeded content (documents, plugins, jobs) for authz tests
- Clean state: reset database, clear caches/rate limiter, and seed a known dataset before each run

## 3) Execution Phases

### Phase A - Preflight
- Verify environment variables set (no default secrets).
- Ensure service auth tokens are non-empty (e.g., `PLUGIN_RUNTIME_TOKEN`, `JOB_WORKER_TOKEN`).
- Record git commit hash being tested.
- Reset/seed the database and confirm test accounts exist and are isolated from prod data.

### Phase B - Automated Static Checks (CI-friendly)

Backend (Python):
- SAST: `bandit -r backend/app`
- Lint for unsafe patterns: `python backend/scripts/check_security_patterns.py`
- SCA: `pip-audit -r backend/requirements.txt`
- Secrets: `gitleaks detect --source backend --no-git`

Frontend (Node/TS):
- SCA: `npm audit --omit=dev` (or `--production`)
- Secrets: `gitleaks detect --source frontend --no-git`

Config/IaC (if present):
- Dockerfiles, compose, k8s manifests: `trivy config .` or `checkov -d .`

### Phase C - Automated Dynamic Checks

API tests (local or staging):
- Authn/Authz regression suite (see matrix below)
- Rate limiting and request size tests
- Internal endpoint access tests
- Convert curl scenarios into pytest regression tests to prevent drift

DAST (staging only):
- Run OWASP ZAP against the exposed API surface and capture report

### Phase D - Manual Targeted Review

- Auth dependency usage and router-level protection
- Plugin file handling and path traversal guards
- Audit logging redaction and request ID correlation
- TrustedHost, CORS, cookie flags, and proxy header trust

### Phase E - Reporting and Sign-off

- PASS/FAIL per roadmap item
- Prioritized findings list with severity
- Evidence bundle (command output, logs, test results)

## 4) Test Matrix (Core)

Authn:
- No token -> 401
- Expired token -> 401
- Tampered token -> 401
- Refresh flow -> 200 only with valid refresh token

Authz:
- User access admin endpoint -> 403
- User access another user's resource -> 403/404
- Service endpoint with user JWT -> 401
- Internal endpoint with service token -> 200

Rate Limiting:
- Login brute force (6+ attempts in window) -> 429
- Authenticated abuse (user_id key) -> 429
- X-Forwarded-For spoofing -> still rate limited

Request Size:
- Oversized JSON body -> 413
- Oversized upload -> 413 (and no partial write)

Audit Logging:
- Auth failure emits audit event (redacted)
- Admin actions emit audit event (redacted)
- X-Request-ID present in responses and logs

Plugin and File Handling:
- Path traversal attempts -> 404
- Zip slip or unsafe extraction -> blocked
- Public plugin endpoints enforce auth where required

Configuration Hardening:
- `SECRET_KEY` non-default enforced in prod-like
- `ENCRYPTION_MASTER_KEY` enforced
- `TrustedHostMiddleware` not `["*"]` in prod-like
- Cookie flags: `HttpOnly`, `Secure`, `SameSite` align to env

Injection:
- SQL injection probes in search/filter params -> no leak
- Log injection via headers -> sanitized

## 5) Evidence and Exit Criteria

Evidence to capture:
- Test command outputs
- DAST reports
- API response logs for each test case
- Audit log samples showing redaction

Exit criteria:
- All roadmap items PASS
- No CRITICAL/HIGH findings open
- MEDIUM findings triaged with a plan and owner

## 6) CI Gating Recommendations

Add a security job that runs on PRs:
- `backend/scripts/check_security_patterns.py`
- `bandit -r backend/app`
- `pip-audit -r backend/requirements.txt`
- `npm audit --omit=dev`
- `gitleaks detect --source . --no-git`
- Security pytest suite (e.g., `pytest -m security`)

Add a nightly job (staging):
- OWASP ZAP report
- Full authn/authz matrix

## 7) Open Questions

- Which environments are in scope for sign-off?
- Do you want these checks to block merges or only generate reports?
- Any compliance targets that require additional controls or evidence?
