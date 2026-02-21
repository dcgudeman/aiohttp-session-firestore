"""Minimal aiohttp app with Firestore-backed sessions.

Run:
    pip install aiohttp-session-firestore
    python examples/basic_app.py

Then visit http://localhost:8080 — the page shows your visit count,
persisted across requests in Firestore.

Prerequisites:
    - A GCP project with Firestore enabled.
    - Application Default Credentials configured
      (``gcloud auth application-default login``).
"""

from aiohttp import web
from aiohttp_session import get_session, setup
from google.cloud.firestore_v1 import AsyncClient

from aiohttp_session_firestore import FirestoreStorage


async def index(request: web.Request) -> web.Response:
    session = await get_session(request)
    visits: int = session.get("visits", 0) + 1
    session["visits"] = visits
    return web.Response(text=f"Visit #{visits}")


async def reset(request: web.Request) -> web.Response:
    session = await get_session(request)
    session.invalidate()
    return web.Response(text="Session cleared.")


def create_app() -> web.Application:
    app = web.Application()

    firestore_client = AsyncClient()
    storage = FirestoreStorage(
        firestore_client,
        max_age=86_400,
        secure=False,  # set True behind HTTPS
        samesite="Lax",
    )
    setup(app, storage)

    app.router.add_get("/", index)
    app.router.add_get("/reset", reset)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=8080)
