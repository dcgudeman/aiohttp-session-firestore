"""Tests for FirestoreStorage."""

from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp_session import Session
from google.cloud.firestore_v1 import AsyncClient

from aiohttp_session_firestore import FirestoreStorage

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_doc_snapshot(
    exists: bool = True, data: dict[str, Any] | None = None
) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data
    return snap


def _make_doc_ref(snapshot: MagicMock | None = None) -> MagicMock:
    ref = MagicMock()
    ref.get = AsyncMock(return_value=snapshot or _make_doc_snapshot(exists=False))
    ref.set = AsyncMock()
    ref.delete = AsyncMock()
    return ref


def _make_storage(
    doc_ref: MagicMock | None = None, **kwargs: Any
) -> tuple[FirestoreStorage, MagicMock]:
    """Build a FirestoreStorage wired to a mock Firestore client.

    Returns the storage and the document-reference mock so tests can
    inspect calls.
    """
    ref = doc_ref or _make_doc_ref()
    collection = MagicMock()
    collection.document.return_value = ref
    client = MagicMock(spec=AsyncClient)
    client.collection.return_value = collection
    storage = FirestoreStorage(client, **kwargs)
    return storage, ref


def _make_request(cookie_value: str | None = None) -> web.Request:
    """Return a minimal mock request with an optional session cookie."""
    req = MagicMock(spec=web.Request)
    if cookie_value is not None:
        req.cookies = {"AIOHTTP_SESSION": cookie_value}
    else:
        req.cookies = {}
    return req


def _make_response() -> web.Response:
    return web.Response()


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_rejects_wrong_client_type(self) -> None:
        with pytest.raises(TypeError, match="AsyncClient"):
            FirestoreStorage("not-a-client")  # type: ignore[arg-type]

    def test_default_collection_name(self) -> None:
        client = MagicMock(spec=AsyncClient)
        FirestoreStorage(client)
        client.collection.assert_called_once_with("aiohttp_sessions")

    def test_custom_collection_name(self) -> None:
        client = MagicMock(spec=AsyncClient)
        FirestoreStorage(client, collection_name="my_sessions")
        client.collection.assert_called_once_with("my_sessions")


# ---------------------------------------------------------------------------
# load_session
# ---------------------------------------------------------------------------


class TestLoadSession:
    async def test_no_cookie_returns_new_session(self) -> None:
        storage, _ = _make_storage()
        session = await storage.load_session(_make_request(cookie_value=None))

        assert session.new is True
        assert session.identity is None

    async def test_cookie_but_missing_document(self) -> None:
        ref = _make_doc_ref(_make_doc_snapshot(exists=False))
        storage, _ = _make_storage(doc_ref=ref)

        session = await storage.load_session(_make_request("abc123"))

        assert session.new is True
        assert session.identity is None

    async def test_cookie_with_valid_document(self) -> None:
        now = int(time.time())
        data = json.dumps({"created": now, "session": {"user": "alice"}})
        snap = _make_doc_snapshot(exists=True, data={"data": data})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, max_age=3600)

        session = await storage.load_session(_make_request("sess-key"))

        assert session.new is False
        assert session.identity == "sess-key"
        assert dict(session) == {"user": "alice"}
        assert session.created == now

    async def test_expired_document_returns_new_session_and_deletes(self) -> None:
        past = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(hours=1)
        data = json.dumps({"created": 1000, "session": {"x": 1}})
        snap = _make_doc_snapshot(exists=True, data={"data": data, "expire": past})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, max_age=60)

        session = await storage.load_session(_make_request("old-key"))

        assert session.new is True
        ref.delete.assert_awaited_once()

    async def test_corrupted_data_returns_new_session(self) -> None:
        snap = _make_doc_snapshot(exists=True, data={"data": "NOT-VALID-JSON!!!"})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref)

        session = await storage.load_session(_make_request("bad-key"))

        assert session.new is True
        assert session.identity is None

    async def test_document_with_none_to_dict(self) -> None:
        snap = _make_doc_snapshot(exists=True, data=None)
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref)

        session = await storage.load_session(_make_request("key"))

        assert session.new is True

    async def test_no_expire_field_treated_as_not_expired(self) -> None:
        now = int(time.time())
        data = json.dumps({"created": now, "session": {"ok": True}})
        snap = _make_doc_snapshot(exists=True, data={"data": data})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, max_age=3600)

        session = await storage.load_session(_make_request("key"))

        assert session.new is False

    async def test_future_expire_treated_as_valid(self) -> None:
        now = int(time.time())
        future = _dt.datetime.now(tz=_dt.UTC) + _dt.timedelta(hours=1)
        data = json.dumps({"created": now, "session": {"ok": True}})
        snap = _make_doc_snapshot(exists=True, data={"data": data, "expire": future})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, max_age=3600)

        session = await storage.load_session(_make_request("key"))

        assert session.new is False

    async def test_naive_expire_datetime_treated_as_utc(self) -> None:
        past_naive = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
        data = json.dumps({"created": 1000, "session": {}})
        snap = _make_doc_snapshot(
            exists=True, data={"data": data, "expire": past_naive}
        )
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, max_age=60)

        session = await storage.load_session(_make_request("key"))

        assert session.new is True
        ref.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# save_session
# ---------------------------------------------------------------------------


class TestSaveSession:
    async def test_new_empty_session_is_noop(self) -> None:
        storage, ref = _make_storage()
        session = Session(None, data=None, new=True, max_age=None)
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        ref.set.assert_not_awaited()
        ref.delete.assert_not_awaited()

    async def test_new_nonempty_session_creates_document(self) -> None:
        fixed_key = "deadbeef"
        storage, ref = _make_storage(key_factory=lambda: fixed_key, max_age=600)
        session = Session(None, data=None, new=True, max_age=600)
        session["user"] = "bob"
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        ref.set.assert_awaited_once()
        written = ref.set.call_args[0][0]
        assert "data" in written
        assert "expire" in written
        parsed = json.loads(written["data"])
        assert parsed["session"]["user"] == "bob"

    async def test_existing_nonempty_session_updates_document(self) -> None:
        storage, ref = _make_storage(max_age=600)
        session = Session("existing-key", data=None, new=False, max_age=600)
        session["count"] = 42
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        ref.set.assert_awaited_once()

    async def test_existing_empty_session_deletes_document(self) -> None:
        storage, ref = _make_storage()
        session = Session("existing-key", data=None, new=False, max_age=None)
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        ref.delete.assert_awaited_once()
        ref.set.assert_not_awaited()

    async def test_no_expire_when_max_age_is_none(self) -> None:
        storage, ref = _make_storage(max_age=None)
        session = Session(None, data=None, new=True, max_age=None)
        session["x"] = 1
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        written = ref.set.call_args[0][0]
        assert "expire" not in written

    async def test_custom_key_factory_is_used(self) -> None:
        call_count = 0

        def counting_factory() -> str:
            nonlocal call_count
            call_count += 1
            return f"custom-{call_count}"

        storage, _ref = _make_storage(key_factory=counting_factory)
        session = Session(None, data=None, new=True, max_age=None)
        session["a"] = 1
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        assert call_count == 1

    async def test_expire_timestamp_is_utc_datetime(self) -> None:
        storage, ref = _make_storage(max_age=3600)
        session = Session(None, data=None, new=True, max_age=3600)
        session["x"] = 1
        response = _make_response()

        before = _dt.datetime.now(tz=_dt.UTC)
        await storage.save_session(_make_request(), response, session)
        after = _dt.datetime.now(tz=_dt.UTC)

        written = ref.set.call_args[0][0]
        expire = written["expire"]
        assert isinstance(expire, _dt.datetime)
        assert expire.tzinfo is not None
        assert before + _dt.timedelta(seconds=3600) <= expire
        assert expire <= after + _dt.timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# _is_expired
# ---------------------------------------------------------------------------


class TestIsExpired:
    def test_no_expire_field(self) -> None:
        assert FirestoreStorage._is_expired({}) is False

    def test_non_datetime_expire(self) -> None:
        assert FirestoreStorage._is_expired({"expire": "not-a-dt"}) is False

    def test_future_expire(self) -> None:
        future = _dt.datetime.now(tz=_dt.UTC) + _dt.timedelta(hours=1)
        assert FirestoreStorage._is_expired({"expire": future}) is False

    def test_past_expire(self) -> None:
        past = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(hours=1)
        assert FirestoreStorage._is_expired({"expire": past}) is True

    def test_naive_past_expire_treated_as_utc(self) -> None:
        past = _dt.datetime.utcnow() - _dt.timedelta(hours=1)
        assert FirestoreStorage._is_expired({"expire": past}) is True


# ---------------------------------------------------------------------------
# Custom encoder / decoder
# ---------------------------------------------------------------------------


class TestCustomEncoderDecoder:
    async def test_custom_encoder_is_used_on_save(self) -> None:
        encoder_called = False
        original_encoder = json.dumps

        def tracking_encoder(obj: object) -> str:
            nonlocal encoder_called
            encoder_called = True
            return original_encoder(obj)

        storage, _ref = _make_storage(encoder=tracking_encoder)
        session = Session(None, data=None, new=True, max_age=None)
        session["x"] = 1
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        assert encoder_called

    async def test_custom_decoder_is_used_on_load(self) -> None:
        decoder_called = False
        original_decoder = json.loads

        def tracking_decoder(s: str) -> Any:
            nonlocal decoder_called
            decoder_called = True
            return original_decoder(s)

        data = json.dumps({"created": 1, "session": {}})
        snap = _make_doc_snapshot(exists=True, data={"data": data})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref, decoder=tracking_decoder)

        await storage.load_session(_make_request("key"))

        assert decoder_called
