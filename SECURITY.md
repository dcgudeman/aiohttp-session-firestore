# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly by
emailing **dcgudeman@gmail.com** rather than opening a public issue.

Include:

- A description of the vulnerability.
- Steps to reproduce.
- Potential impact.

You can expect an acknowledgement within 48 hours and a follow-up within
7 days with next steps.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Security Considerations

- **Cookie flags:** Always set `secure=True` and `samesite="Lax"` (or
  `"Strict"`) in production to mitigate session hijacking and CSRF.
- **Session data:** Do not store secrets (passwords, API keys, tokens) in the
  session. Sessions are for user state only.
- **Encryption at rest:** Firestore encrypts data at rest using Google-managed
  keys. For additional protection, provide a custom `encoder`/`decoder` that
  encrypts session data at the application level.
- **Session IDs:** Session keys default to `secrets.token_urlsafe(32)`, using
  cryptographically secure randomness. Custom `key_factory` callables must also
  produce cryptographically unpredictable keys that are valid single Firestore
  document IDs. Malformed session cookies are treated as missing sessions.
- **Logout:** Saving a stale session cannot recreate a document deleted by logout.
  Concurrent updates to a session that still exists remain last-writer-wins.
