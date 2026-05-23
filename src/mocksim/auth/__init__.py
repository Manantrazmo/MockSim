"""
mocksim.auth — operator session authentication for the dashboard.

Replaces the localStorage-pasted bearer token with a proper username +
password login that returns a signed HTTP-only cookie. JS in the browser
can't read the cookie, which means an XSS bug in the dashboard can't
steal credentials.

Service-to-service callers (trazmo's disbursement adapter, scripts,
curl, the seed_e2e CLI) keep using `Authorization: Bearer <token>` —
that's the right model for machine clients. The TenancyMiddleware
accepts either auth method on /admin/* endpoints.

Module layout
─────────────
  password.py   bcrypt hash + verify
  bootstrap.py  ensure default admin user exists on startup
  api.py        POST /auth/login, POST /auth/logout, GET /auth/me
"""
