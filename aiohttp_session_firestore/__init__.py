"""Google Cloud Firestore session storage backend for aiohttp-session."""

from __future__ import annotations

import datetime as _dt
import json
import secrets
from collections.abc import Mapping
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING, Any

from aiohttp_session import AbstractStorage, Session
from google.api_core.exceptions import FailedPrecondition, NotFound
from google.cloud.firestore_v1 import (
    DELETE_FIELD,
    AsyncClient,
    AsyncCollectionReference,
    LastUpdateOption,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiohttp import web

__all__ = ["FirestoreStorage"]
__version__ = "0.1.3"


def _firestore_json_default(obj: Any) -> Any:
    """``json.dumps`` *default* hook that serializes Firestore timestamps.

    Firestore returns :class:`~google.api_core.datetime_helpers.DatetimeWithNanoseconds`
    objects, which are not natively JSON-serializable.  This hook converts
    any :class:`~datetime.datetime` instance to a millisecond-precision
    Unix timestamp (int).
    """
    if isinstance(obj, _dt.datetime):
        return int(obj.timestamp() * 1000)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


_default_encoder: Callable[[object], str] = partial(
    json.dumps, default=_firestore_json_default
)


class FirestoreStorage(AbstractStorage):
    """Server-side session storage using Google Cloud Firestore.

    Each session is persisted as a document in the configured Firestore
    collection.  The HTTP cookie holds only an opaque session key; all
    session data lives server-side in Firestore.

    By default, new session document IDs are generated with
    ``secrets.token_urlsafe(32)`` (256 bits of randomness). Supply a custom
    ``key_factory`` to override this (e.g. ``lambda: uuid.uuid4().hex``).

    An ``expire`` field is written as a UTC :class:`~datetime.datetime` so
    that a `Firestore TTL policy`_ can automatically delete stale documents.
    Expiration is *also* checked on every read so sessions are treated as
    expired immediately, even before the TTL policy runs.

    .. _Firestore TTL policy:
       https://cloud.google.com/firestore/docs/ttl

    Parameters
    ----------
    client:
        A :class:`google.cloud.firestore_v1.AsyncClient` instance.
    collection_name:
        Firestore collection used to store session documents.
    key_factory:
        Zero-argument callable that returns a new session key string.
        Defaults to ``None``, which uses a cryptographically secure token.

    All remaining keyword arguments are forwarded to
    :class:`aiohttp_session.AbstractStorage` (cookie name, domain,
    max_age, secure, httponly, samesite, encoder, decoder).
    """

    def __init__(
        self,
        client: AsyncClient,
        *,
        collection_name: str = "aiohttp_sessions",
        key_factory: Callable[[], str] | None = None,
        cookie_name: str = "__session",
        domain: str | None = None,
        max_age: int | None = None,
        path: str = "/",
        secure: bool | None = None,
        httponly: bool = True,
        samesite: str | None = None,
        encoder: Callable[[object], str] = _default_encoder,
        decoder: Callable[[str], Any] = json.loads,
    ) -> None:
        super().__init__(
            cookie_name=cookie_name,
            domain=domain,
            max_age=max_age,
            path=path,
            secure=secure,
            httponly=httponly,
            samesite=samesite,
            encoder=encoder,
            decoder=decoder,
        )
        if not isinstance(client, AsyncClient):
            raise TypeError(
                f"Expected google.cloud.firestore_v1.AsyncClient, got {type(client)}"
            )
        self._collection: AsyncCollectionReference = client.collection(collection_name)
        self._key_factory = key_factory

    async def load_session(self, request: web.Request) -> Session:
        """Load a session from Firestore.

        Returns a new empty session when:
        * no session cookie is present,
        * the referenced document does not exist,
        * the document has expired (server-side check), or
        * the stored data cannot be decoded or has an invalid structure.
        """
        cookie = self.load_cookie(request)
        if cookie is None or not self._is_valid_key(cookie):
            return Session(None, data=None, new=True, max_age=self.max_age)

        key = cookie
        doc_ref = self._collection.document(key)
        doc = await doc_ref.get()

        if not doc.exists:
            return Session(None, data=None, new=True, max_age=self.max_age)

        doc_dict = doc.to_dict()
        if doc_dict is None:
            return Session(None, data=None, new=True, max_age=self.max_age)

        if self._is_expired(doc_dict):
            # Delete only the version we read: another request may have
            # refreshed or removed the document since the snapshot.
            with suppress(FailedPrecondition, NotFound):
                await doc_ref.delete(option=LastUpdateOption(doc.update_time))
            return Session(None, data=None, new=True, max_age=self.max_age)

        try:
            data = self._decoder(doc_dict.get("data", "{}"))
        except (ValueError, TypeError):
            return Session(None, data=None, new=True, max_age=self.max_age)

        if (
            not isinstance(data, Mapping)
            or type(data.get("created")) is not int
            or not isinstance(data.get("session"), Mapping)
        ):
            return Session(None, data=None, new=True, max_age=self.max_age)

        max_age = doc_dict.get("max_age", self.max_age)
        if max_age is not None and type(max_age) is not int:
            return Session(None, data=None, new=True, max_age=self.max_age)

        # A save refreshes expire even when session.changed() leaves created
        # unchanged. Do not let Session's age check override that deadline.
        has_expire = isinstance(doc_dict.get("expire"), _dt.datetime)
        session = Session(
            key,
            data={"created": data["created"], "session": dict(data["session"])},
            new=False,
            max_age=None if has_expire else max_age,
        )
        session.max_age = max_age
        return session

    async def save_session(
        self,
        request: web.Request,
        response: web.StreamResponse,
        session: Session,
    ) -> None:
        """Persist a session to Firestore.

        * **New empty session** -- no document is written and no cookie is set
          (avoids unnecessary Firestore writes).
        * **Existing empty session** -- the document is deleted and the cookie
          is cleared.
        * **Non-empty session** -- data is written and the cookie is set.
          An existing session deleted by another request is not recreated.
        """
        key = session.identity

        if key is None:
            if session.empty:
                return
            key = self._generate_key()
        else:
            if not self._is_valid_key(key):
                raise ValueError("Session key must be a valid Firestore document ID")
            if session.empty:
                self.save_cookie(response, "", max_age=session.max_age)
                await self._collection.document(key).delete()
                return

        data_str = self._encoder(self._get_session_data(session))
        doc_data: dict[str, Any] = {"data": data_str, "max_age": session.max_age}

        if session.max_age is not None:
            doc_data["expire"] = _dt.datetime.now(
                tz=_dt.UTC,
            ) + _dt.timedelta(seconds=session.max_age)

        doc_ref = self._collection.document(key)
        if session.new or session.identity is None:
            await doc_ref.set(doc_data)
        else:
            if session.max_age is None:
                doc_data["expire"] = DELETE_FIELD
            try:
                # update requires the document to exist, so an in-flight
                # request cannot restore a session deleted by logout.
                await doc_ref.update(doc_data)
            except NotFound:
                self.save_cookie(response, "")
                return
        self.save_cookie(response, key, max_age=session.max_age)
        if session.max_age is None:
            # AbstractStorage treats None as "use the storage default".
            # Here it means this session explicitly has no cookie lifetime.
            response.cookies[self.cookie_name]["max-age"] = ""
            response.cookies[self.cookie_name]["expires"] = ""

    def _generate_key(self) -> str:
        """Return a new session key.

        Uses the caller-supplied ``key_factory`` if provided, otherwise
        generates a cryptographically secure token.
        """
        key = (
            self._key_factory()
            if self._key_factory is not None
            else secrets.token_urlsafe(32)
        )
        if not self._is_valid_key(key):
            raise ValueError("Session key must be a valid Firestore document ID")
        return key

    @staticmethod
    def _is_valid_key(key: object) -> bool:
        """Accept a single Firestore document ID, including legacy session IDs."""
        if (
            not isinstance(key, str)
            or not key
            or "/" in key
            or key in {".", ".."}
            or (key.startswith("__") and key.endswith("__"))
        ):
            return False
        try:
            return len(key.encode("utf-8")) <= 1500
        except UnicodeEncodeError:
            return False

    @staticmethod
    def _is_expired(doc_dict: dict[str, Any]) -> bool:
        """Return ``True`` if the document's ``expire`` timestamp is in the past."""
        expire = doc_dict.get("expire")
        if not isinstance(expire, _dt.datetime):
            return False
        now = _dt.datetime.now(tz=_dt.UTC)
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=_dt.UTC)
        return now >= expire
