# aiohttp-session-firestore

[![CI](https://github.com/dcgudeman/aiohttp-session-firestore/actions/workflows/ci.yml/badge.svg)](https://github.com/dcgudeman/aiohttp-session-firestore/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aiohttp-session-firestore)](https://pypi.org/project/aiohttp-session-firestore/)
[![Python](https://img.shields.io/pypi/pyversions/aiohttp-session-firestore)](https://pypi.org/project/aiohttp-session-firestore/)
[![License](https://img.shields.io/github/license/dcgudeman/aiohttp-session-firestore)](LICENSE)

**Google Cloud Firestore** session storage backend for
[aiohttp-session](https://github.com/aio-libs/aiohttp-session).

Drop-in, async, server-side sessions — the cookie holds only an opaque key
while all session data lives in Firestore.

---

## Installation

```bash
pip install aiohttp-session-firestore
```

## Quick start

```python
from aiohttp import web
from aiohttp_session import setup, get_session
from google.cloud.firestore_v1 import AsyncClient

from aiohttp_session_firestore import FirestoreStorage

async def handler(request: web.Request) -> web.Response:
    session = await get_session(request)
    session["visits"] = session.get("visits", 0) + 1
    return web.Response(text=f"Visits: {session['visits']}")

def create_app() -> web.Application:
    app = web.Application()
    firestore_client = AsyncClient()
    storage = FirestoreStorage(firestore_client, max_age=86400)
    setup(app, storage)
    app.router.add_get("/", handler)
    return app

if __name__ == "__main__":
    web.run_app(create_app())
```

## Configuration

`FirestoreStorage` accepts every parameter that
`aiohttp_session.AbstractStorage` does, plus a few Firestore-specific ones:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `client` | `AsyncClient` | *(required)* | Firestore async client instance |
| `collection_name` | `str` | `"aiohttp_sessions"` | Firestore collection for session documents |
| `key_factory` | `(() -> str) \| None` | `None` | Callable that produces new session keys. `None` uses Firestore auto-generated IDs. |
| `cookie_name` | `str` | `"AIOHTTP_SESSION"` | Name of the HTTP cookie |
| `max_age` | `int \| None` | `None` | Session lifetime in seconds (`None` = browser session) |
| `secure` | `bool \| None` | `None` | `Secure` cookie flag — **set to `True` in production** |
| `httponly` | `bool` | `True` | `HttpOnly` cookie flag |
| `samesite` | `str \| None` | `None` | `SameSite` cookie attribute (`"Lax"`, `"Strict"`, or `"None"`) |
| `domain` | `str \| None` | `None` | Cookie domain |
| `path` | `str` | `"/"` | Cookie path |
| `encoder` | `(object) -> str` | Firestore-aware `json.dumps` | Session data encoder (handles `DatetimeWithNanoseconds`) |
| `decoder` | `(str) -> Any` | `json.loads` | Session data decoder |

### Production recommendations

```python
storage = FirestoreStorage(
    client,
    max_age=86400,       # 24-hour sessions
    secure=True,         # HTTPS only
    httponly=True,        # default — prevents JS access
    samesite="Lax",      # CSRF protection
)
```

## Session expiration & Firestore TTL

When `max_age` is set, each session document is written with an `expire` field
containing a UTC `datetime`. The library checks this field on every read and
treats expired documents as missing immediately.

For **automatic cleanup** of expired documents, configure a
[Firestore TTL policy](https://cloud.google.com/firestore/docs/ttl) on the
`expire` field:

```bash
gcloud firestore fields ttls update expire \
    --collection-group=aiohttp_sessions \
    --enable-ttl
```

> **Note:** Firestore's TTL deletion is best-effort and may take up to
> 72 hours. The server-side expiration check in `load_session` ensures
> correctness regardless of TTL policy timing.

## Document structure

Each session is stored as a Firestore document:

```
aiohttp_sessions/{firestore-auto-id}
├── data: '{"created": 1700000000, "session": {"user": "alice"}}'
└── expire: 2024-11-15T12:00:00Z   (only when max_age is set)
```

The `data` field contains a JSON-encoded string (controlled by the
`encoder`/`decoder` parameters). The `expire` field is a native Firestore
`Timestamp`, compatible with TTL policies.

## Firestore costs

Each request that touches the session incurs:

| Operation | Firestore cost |
|---|---|
| Load (no cookie) | 0 reads (short-circuits before Firestore call) |
| Load (cookie, doc exists) | 1 document read |
| Load (cookie, doc missing) | 1 document read |
| Save (non-empty session) | 1 document write |
| Save (empty new session) | 0 writes (skipped) |
| Save (empty existing session) | 1 document delete |

The library avoids unnecessary writes — new sessions that remain empty are
never persisted.

## Default encoder

The default encoder is a Firestore-aware `json.dumps` that automatically
converts `datetime` objects (including Firestore's `DatetimeWithNanoseconds`)
to millisecond Unix timestamps. This prevents serialization errors when session
data contains values that originated from Firestore queries. To override, pass
a custom `encoder`.

## Limitations

- **Session data must be JSON-serializable** (or serializable by your custom
  encoder). Avoid storing large blobs; Firestore documents are limited to
  1 MiB.
- **Eventual consistency:** Firestore in Datastore mode uses eventual
  consistency for some queries. This library reads by document ID (strongly
  consistent in both Native and Datastore mode).
- **No built-in encryption:** Firestore encrypts data at rest (Google-managed
  keys). If you need app-level encryption, provide a custom `encoder`/`decoder`
  pair that encrypts before writing and decrypts after reading.
- **Sensitive data:** Avoid storing secrets (passwords, tokens) in sessions.
  Sessions are intended for user state, not credential storage.

## Development

```bash
# Clone and install dev dependencies
git clone https://github.com/dcgudeman/aiohttp-session-firestore.git
cd aiohttp-session-firestore
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run checks
ruff check .
ruff format --check .
mypy aiohttp_session_firestore
pytest --cov
```

## License

[Apache 2.0](LICENSE)
