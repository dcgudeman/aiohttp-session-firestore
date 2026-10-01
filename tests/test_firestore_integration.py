"""Small HTTP integration suite, enabled only for a local Firestore emulator."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import get_session, setup
from google.cloud.firestore_v1 import AsyncClient

from aiohttp_session_firestore import FirestoreStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from google.cloud.firestore_v1 import AsyncCollectionReference


@pytest_asyncio.fixture
async def backend() -> AsyncIterator[tuple[AsyncClient, AsyncCollectionReference]]:
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
    try:
        endpoint = urlsplit(f"//{host}")
        port = endpoint.port
    except ValueError:
        pytest.skip("FIRESTORE_EMULATOR_HOST must be a local host:port")
    if endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("Set FIRESTORE_EMULATOR_HOST to a local emulator host:port")
    if not port or endpoint.path or endpoint.username or endpoint.password:
        pytest.skip("FIRESTORE_EMULATOR_HOST must be a local host:port")

    client = AsyncClient(project="demo-aiohttp-session-firestore")
    collection = client.collection(f"session_test_{uuid4().hex}")
    try:
        yield client, collection
    finally:
        try:
            async with asyncio.timeout(10):
                async for doc in collection.stream():
                    await doc.reference.delete()
        finally:
            client.close()  # type: ignore[no-untyped-call]


def _make_app(storage: FirestoreStorage) -> web.Application:
    app = web.Application()
    setup(app, storage)

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["user"] = "alice"
        return web.json_response(dict(session))

    async def read(request: web.Request) -> web.Response:
        return web.json_response(dict(await get_session(request)))

    async def logout(request: web.Request) -> web.Response:
        session = await get_session(request)
        session.invalidate()
        return web.Response(status=204)

    app.router.add_post("/login", login)
    app.router.add_get("/", read)
    app.router.add_post("/logout", logout)
    return app


async def test_http_session_persistence_and_logout(
    backend: tuple[AsyncClient, AsyncCollectionReference],
) -> None:
    client, collection = backend
    storage = FirestoreStorage(client, collection_name=collection.id, max_age=600)
    async with (
        asyncio.timeout(15),
        TestClient(
            TestServer(_make_app(storage)), cookie_jar=CookieJar(unsafe=True)
        ) as http,
    ):
        response = await http.post("/login")
        assert response.status == 200
        cookie = response.cookies["__session"]
        assert cookie["httponly"]
        assert cookie["max-age"] == "600"
        doc_ref = collection.document(cookie.value)
        assert (await doc_ref.get()).exists

        response = await http.get("/")
        assert response.status == 200
        assert await response.json() == {"user": "alice"}

        response = await http.post("/logout")
        assert response.status == 204
        assert response.cookies["__session"].value == ""
        assert not (await doc_ref.get()).exists

        response = await http.get("/")
        assert await response.json() == {}


@pytest.mark.parametrize("max_age", [None, 60, 3600])
async def test_http_session_lifetime_override(
    backend: tuple[AsyncClient, AsyncCollectionReference], max_age: int | None
) -> None:
    client, collection = backend
    storage = FirestoreStorage(client, collection_name=collection.id, max_age=600)
    app = _make_app(storage)

    async def lifetime(request: web.Request) -> web.Response:
        session = await get_session(request)
        if request.method == "POST":
            session.max_age = max_age
            session.changed()
        return web.json_response({"max_age": session.max_age, "user": session["user"]})

    app.router.add_post("/lifetime", lifetime)
    app.router.add_get("/lifetime", lifetime)
    async with (
        asyncio.timeout(15),
        TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as http,
    ):
        response = await http.post("/login")
        assert response.status == 200
        doc_ref = collection.document(response.cookies["__session"].value)
        response = await http.post("/lifetime")
        assert response.status == 200
        cookie = response.cookies["__session"]
        document = (await doc_ref.get()).to_dict()
        assert document is not None
        assert document["max_age"] == max_age
        if max_age is None:
            assert cookie["max-age"] == cookie["expires"] == ""
            assert "expire" not in document
        else:
            assert cookie["max-age"] == str(max_age)
            assert "expire" in document

        response = await http.get("/lifetime")
        assert response.status == 200
        assert await response.json() == {"max_age": max_age, "user": "alice"}


async def test_logout_cannot_be_undone_by_a_stale_request(
    backend: tuple[AsyncClient, AsyncCollectionReference],
) -> None:
    client, collection = backend
    storage = FirestoreStorage(client, collection_name=collection.id, max_age=600)
    app = _make_app(storage)
    loaded = asyncio.Event()
    resume = asyncio.Event()

    async def stale_save(request: web.Request) -> web.Response:
        session = await get_session(request)
        assert session["user"] == "alice"
        loaded.set()
        await resume.wait()
        session["counter"] = 1
        return web.Response(status=204)

    app.router.add_post("/stale", stale_save)
    async with (
        asyncio.timeout(15),
        TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as http,
    ):
        response = await http.post("/login")
        assert response.status == 200
        key = response.cookies["__session"].value
        doc_ref = collection.document(key)

        stale_request = asyncio.ensure_future(http.post("/stale"))
        try:
            await loaded.wait()
            response = await http.post("/logout")
            assert response.status == 204
            assert not (await doc_ref.get()).exists
        finally:
            resume.set()
            stale_response = await stale_request

        assert stale_response.status == 204
        assert not (await doc_ref.get()).exists
        response = await http.get("/", cookies={"__session": key})
        assert await response.json() == {}
