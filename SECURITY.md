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
- **Session IDs:** Session keys default to Firestore auto-generated IDs
  (20-character alphanumeric, designed for even distribution). A custom
  `key_factory` (e.g. `lambda: uuid.uuid4().hex`) can be supplied if
  preferred. Both approaches produce unpredictable, collision-resistant keys.
