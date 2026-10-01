"""Tests for FirestoreStorage."""

from __future__ import annotations

import datetime as _dt
import json
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from aiohttp import web
from aiohttp_session import Session
from google.api_core.exceptions import NotFound, ServiceUnavailable
from google.cloud.firestore_v1 import DELETE_FIELD, AsyncClient

from aiohttp_session_firestore import (
    FirestoreStorage,
    _default_encoder,
    _firestore_json_default,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _make_doc_ref(
    snapshot: MagicMock | None = None, *, doc_id: str = "auto-id"
) -> MagicMock:
    ref = MagicMock()
    ref.get = AsyncMock(return_value=snapshot or _make_doc_snapshot(exists=False))
    ref.set = AsyncMock()
    ref.update = AsyncMock()
    ref.delete = AsyncMock()
    type(ref).id = PropertyMock(return_value=doc_id)
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
        req.cookies = {"__session": cookie_value}
    else:
        req.cookies = {}
    return req


def _make_response() -> web.Response:
    return web.Response()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Advance both the storage and aiohttp-session clocks without sleeping."""
    timestamp = 1_700_000_000

    class FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz: _dt.tzinfo | None = None) -> FrozenDatetime:
            return cls.fromtimestamp(timestamp, tz)

    monkeypatch.setattr(_dt, "datetime", FrozenDatetime)
    monkeypatch.setattr(time, "time", lambda: timestamp)

    def advance(seconds: int) -> None:
        nonlocal timestamp
        timestamp += seconds

    return advance


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
    @pytest.mark.parametrize(
        "cookie",
        [
            "",
            "a/b",
            "a/b/c",
            ".",
            "..",
            "__reserved__",
            "x" * 1501,
            "é" * 751,
            "\ud800",
        ],
    )
    async def test_invalid_cookie_never_accesses_firestore(self, cookie: str) -> None:
        storage, ref = _make_storage()

        session = await storage.load_session(_make_request(cookie))

        assert session.new is True
        assert session.identity is None
        storage._collection.document.assert_not_called()  # type: ignore[attr-defined]
        ref.get.assert_not_awaited()

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

    async def test_document_missing_data_key_returns_empty_session(self) -> None:
        snap = _make_doc_snapshot(exists=True, data={"other_field": "value"})
        ref = _make_doc_ref(snap)
        storage, _ = _make_storage(doc_ref=ref)

        session = await storage.load_session(_make_request("key"))

        assert session.new is True

    async def test_naive_expire_datetime_treated_as_utc(self) -> None:
        past_naive = _dt.datetime.now(tz=_dt.UTC).replace(tzinfo=None) - _dt.timedelta(
            hours=1
        )
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

        ref.update.assert_awaited_once()
        ref.set.assert_not_awaited()

    async def test_deleted_session_is_not_recreated(self) -> None:
        storage, ref = _make_storage()
        ref.update.side_effect = NotFound("Session deleted")  # type: ignore[no-untyped-call]
        session = Session("old-key", data=None, new=False)
        session["user"] = "alice"
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        ref.update.assert_awaited_once()
        ref.set.assert_not_awaited()
        assert response.cookies["__session"].value == ""
        assert response.cookies["__session"]["max-age"] == "0"

    async def test_update_backend_failure_is_not_treated_as_logout(self) -> None:
        storage, ref = _make_storage()
        ref.update.side_effect = ServiceUnavailable("Unavailable")  # type: ignore[no-untyped-call]
        session = Session("key", data=None, new=False)
        session["user"] = "alice"
        response = _make_response()

        with pytest.raises(ServiceUnavailable):
            await storage.save_session(_make_request(), response, session)

        assert not response.cookies
        ref.set.assert_not_awaited()

    async def test_new_session_with_explicit_identity_is_created(self) -> None:
        storage, ref = _make_storage()
        session = Session(None, data=None, new=True)
        session.set_new_identity("custom-key")
        session["user"] = "alice"

        await storage.save_session(_make_request(), _make_response(), session)

        ref.set.assert_awaited_once()
        ref.update.assert_not_awaited()

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

    async def test_default_key_uses_cryptographic_randomness(self) -> None:
        storage, ref = _make_storage()
        session = Session(None, data=None, new=True, max_age=None)
        session["x"] = 1
        response = _make_response()

        with patch(
            "aiohttp_session_firestore.secrets.token_urlsafe", return_value="secure-key"
        ) as token:
            await storage.save_session(_make_request(), response, session)

        token.assert_called_once_with(32)
        assert response.cookies["__session"].value == "secure-key"
        ref.set.assert_awaited_once()

    @pytest.mark.parametrize("key", [None, 123, "", "a/b/c", "x" * 1501])
    async def test_invalid_custom_key_fails_before_writing(self, key: Any) -> None:
        storage, ref = _make_storage(key_factory=lambda: key)
        session = Session(None, data=None, new=True)
        session["x"] = 1
        response = _make_response()

        with pytest.raises(ValueError, match="valid Firestore document ID"):
            await storage.save_session(_make_request(), response, session)

        assert not response.cookies
        ref.set.assert_not_awaited()
        ref.update.assert_not_awaited()

    async def test_new_nonempty_session_sets_cookie(self) -> None:
        fixed_key = "mykey"
        storage, _ref = _make_storage(key_factory=lambda: fixed_key, max_age=600)
        session = Session(None, data=None, new=True, max_age=600)
        session["x"] = 1
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        cookie = response.cookies.get("__session")
        assert cookie is not None
        assert cookie.value == fixed_key

    async def test_existing_empty_session_clears_cookie(self) -> None:
        storage, _ref = _make_storage()
        session = Session("old-key", data=None, new=False, max_age=None)
        response = _make_response()

        await storage.save_session(_make_request(), response, session)

        cookie = response.cookies.get("__session")
        assert cookie is not None
        assert cookie.value == ""

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
# Per-session expiration
# ---------------------------------------------------------------------------


class TestSessionLifetime:
    @pytest.mark.parametrize(
        ("default_age", "session_age", "elapsed"),
        [(60, 3600, 120), (3600, 60, 30), (60, None, 86400), (None, 60, 30)],
    )
    async def test_lifetime_override_survives_load_and_expires_on_time(
        self,
        clock: Callable[[int], None],
        default_age: int | None,
        session_age: int | None,
        elapsed: int,
    ) -> None:
        storage, ref = _make_storage(max_age=default_age)
        session = Session(None, data=None, new=True, max_age=default_age)
        session["user"] = "alice"
        session.max_age = session_age
        response = _make_response()

        await storage.save_session(_make_request(), response, session)
        written = ref.set.call_args.args[0]
        ref.get.return_value = _make_doc_snapshot(data=written)
        assert written["max_age"] == session_age
        cookie = response.cookies["__session"]
        if session_age is None:
            assert "expire" not in written
            assert cookie["max-age"] == cookie["expires"] == ""
        else:
            assert cookie["max-age"] == str(session_age)

        clock(elapsed)
        loaded = await storage.load_session(_make_request(cookie.value))
        assert loaded["user"] == "alice"
        assert loaded.max_age == session_age

        if session_age is not None:
            clock(session_age - elapsed)
            expired = await storage.load_session(_make_request(cookie.value))
            assert expired.new is True
            assert expired.identity is None
            assert expired.empty

    async def test_changed_only_save_uses_refreshed_deadline(
        self, clock: Callable[[int], None]
    ) -> None:
        storage, ref = _make_storage(max_age=60)
        session = Session(None, data=None, new=True, max_age=60)
        session["preferences"] = {"theme": "light"}
        await storage.save_session(_make_request(), _make_response(), session)
        ref.get.return_value = _make_doc_snapshot(data=ref.set.call_args.args[0])

        clock(50)
        loaded = await storage.load_session(_make_request("key"))
        loaded["preferences"]["theme"] = "dark"
        loaded.changed()
        await storage.save_session(_make_request(), _make_response(), loaded)
        ref.get.return_value = _make_doc_snapshot(data=ref.update.call_args.args[0])

        clock(20)  # Past created + 60, but before the refreshed expiration.
        refreshed = await storage.load_session(_make_request("key"))
        assert refreshed["preferences"] == {"theme": "dark"}
        assert refreshed.max_age == 60

    async def test_lifetime_only_change_refreshes_deadline(
        self, clock: Callable[[int], None]
    ) -> None:
        storage, ref = _make_storage(max_age=60)
        data = json.dumps({"created": int(time.time()), "session": {"user": "alice"}})
        ref.get.return_value = _make_doc_snapshot(data={"data": data})
        clock(50)
        session = await storage.load_session(_make_request("key"))
        session.max_age = 120
        session.changed()

        await storage.save_session(_make_request(), _make_response(), session)
        ref.get.return_value = _make_doc_snapshot(data=ref.update.call_args.args[0])

        clock(80)
        loaded = await storage.load_session(_make_request("key"))
        assert loaded["user"] == "alice"
        assert loaded.max_age == 120

    async def test_removing_lifetime_deletes_previous_expiration(
        self, clock: Callable[[int], None]
    ) -> None:
        storage, ref = _make_storage(max_age=60)
        session = Session("key", data=None, new=False, max_age=60)
        session["user"] = "alice"
        session.max_age = None
        response = _make_response()
        response.set_cookie("__session", "key", expires="old-expiration", max_age=60)

        await storage.save_session(_make_request(), response, session)

        written = ref.update.call_args.args[0]
        assert written["max_age"] is None
        assert written["expire"] is DELETE_FIELD
        cookie = response.cookies["__session"]
        assert cookie["max-age"] == cookie["expires"] == ""

    async def test_legacy_document_uses_default_lifetime(
        self, clock: Callable[[int], None]
    ) -> None:
        data = json.dumps({"created": int(time.time()), "session": {"user": "alice"}})
        storage, _ = _make_storage(
            doc_ref=_make_doc_ref(_make_doc_snapshot(data={"data": data})), max_age=60
        )

        clock(30)
        loaded = await storage.load_session(_make_request("legacy-auto-ID-12345"))
        assert loaded["user"] == "alice"
        assert loaded.max_age == 60

        clock(31)
        expired = await storage.load_session(_make_request("legacy-auto-ID-12345"))
        assert expired.empty


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
        past = _dt.datetime.now(tz=_dt.UTC).replace(tzinfo=None) - _dt.timedelta(
            hours=1
        )
        assert FirestoreStorage._is_expired({"expire": past}) is True


# ---------------------------------------------------------------------------
# Round-trip (save then load)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    async def test_save_then_load_preserves_session_data(self) -> None:
        """Save a session, then load it back and verify the data survives."""
        captured: dict[str, Any] = {}

        save_ref = MagicMock()
        save_ref.set = AsyncMock(side_effect=lambda doc: captured.update(doc))
        save_ref.delete = AsyncMock()
        type(save_ref).id = PropertyMock(return_value="round-trip-key")

        save_collection = MagicMock()
        save_collection.document.return_value = save_ref
        client = MagicMock(spec=AsyncClient)
        client.collection.return_value = save_collection
        storage = FirestoreStorage(client, max_age=3600)

        session = Session(None, data=None, new=True, max_age=3600)
        session["user"] = "alice"
        session["count"] = 7
        response = _make_response()
        await storage.save_session(_make_request(), response, session)

        load_snap = _make_doc_snapshot(exists=True, data=captured)
        load_ref = _make_doc_ref(load_snap)
        save_collection.document.return_value = load_ref

        loaded = await storage.load_session(_make_request("round-trip-key"))

        assert loaded.new is False
        assert loaded.identity == "round-trip-key"
        assert dict(loaded) == {"user": "alice", "count": 7}


# ---------------------------------------------------------------------------
# Default encoder / _firestore_json_default
# ---------------------------------------------------------------------------


class TestFirestoreJsonDefault:
    def test_datetime_converted_to_millis(self) -> None:
        dt = _dt.datetime(2024, 1, 15, 12, 0, 0, tzinfo=_dt.UTC)
        result = _firestore_json_default(dt)
        assert result == int(dt.timestamp() * 1000)

    def test_naive_datetime_converted_to_millis(self) -> None:
        dt = _dt.datetime(2024, 1, 15, 12, 0, 0)
        result = _firestore_json_default(dt)
        assert result == int(dt.timestamp() * 1000)

    def test_non_datetime_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="not JSON serializable"):
            _firestore_json_default(object())

    def test_default_encoder_handles_datetime_in_session(self) -> None:
        dt = _dt.datetime(2024, 6, 1, tzinfo=_dt.UTC)
        data = {"created": 100, "session": {"ts": dt}}
        result = json.loads(_default_encoder(data))
        assert result["session"]["ts"] == int(dt.timestamp() * 1000)


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
