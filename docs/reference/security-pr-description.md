# Draft PR Description (Final Status)

## Summary
This PR completes the security hardening work from the audit plan and verifies fixes with automated tests and pen-test retests.

Key changes:
- Production safety guards for SECRET_KEY, ENCRYPTION_MASTER_KEY, service tokens, and allowed hosts
- Trusted proxy handling for rate limiting (no XFF spoofing from untrusted clients)
- TrustedHostMiddleware restricted outside dev
- Sanitized token/cookie logging; debug-gated token previews
- Plugin static asset path traversal fixed; public assets gated outside dev
- Request ID middleware and redacted request logs
- Settings instance ownership enforcement (IDOR fix)
- Internal service auth hardened to return 401 instead of 500
- Navigation routes SQL parameterized
- Dev-only cookie endpoints gated

## Testing
- pytest -m security (latest run after fixes)
- Pen-test matrix rerun: all pass; XFF behavior is config-dependent

## Notes
- Production requires SECRET_KEY, ENCRYPTION_MASTER_KEY, service tokens, and non-* FORWARDED_ALLOW_IPS
- If cross-site cookies are required, set COOKIE_SAMESITE=none with COOKIE_SECURE=true
- XFF behavior depends on FORWARDED_ALLOW_IPS (proxy allowlist vs direct access)
