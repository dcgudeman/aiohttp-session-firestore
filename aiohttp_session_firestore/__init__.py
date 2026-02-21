"""Google Cloud Firestore session storage backend for aiohttp-session."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import TYPE_CHECKING, Any

from aiohttp_session import AbstractStorage, Session
from google.cloud.firestore_v1 import AsyncClient, AsyncCollectionReference

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiohttp import web

__all__ = ["FirestoreStorage"]
__version__ = "0.1.0"


class FirestoreStorage(AbstractStorage):
    """Server-side session storage using Google Cloud Firestore.

    Each session is persisted as a document in the configured Firestore
    collection.  The HTTP cookie holds only an opaque session key (a UUID
    by default); all session data lives server-side in Firestore.

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
        Defaults to ``uuid.uuid4().hex``.

    All remaining keyword arguments are forwarded to
    :class:`aiohttp_session.AbstractStorage` (cookie name, domain,
    max_age, secure, httponly, samesite, encoder, decoder).
    """

    def __init__(
        self,
        client: AsyncClient,
        *,
        collection_name: str = "aiohttp_sessions",
        key_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        cookie_name: str = "AIOHTTP_SESSION",
        domain: str | None = None,
        max_age: int | None = None,
        path: str = "/",
        secure: bool | None = None,
        httponly: bool = True,
        samesite: str | None = None,
        encoder: Callable[[object], str] = json.dumps,
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
        * the stored data cannot be decoded.
        """
        cookie = self.load_cookie(request)
        if cookie is None:
            return Session(None, data=None, new=True, max_age=self.max_age)

        key = str(cookie)
        doc_ref = self._collection.document(key)
        doc = await doc_ref.get()

        if not doc.exists:
            return Session(None, data=None, new=True, max_age=self.max_age)

        doc_dict = doc.to_dict()
        if doc_dict is None:
            return Session(None, data=None, new=True, max_age=self.max_age)

        if self._is_expired(doc_dict):
            await doc_ref.delete()
            return Session(None, data=None, new=True, max_age=self.max_age)

        try:
            data = self._decoder(doc_dict.get("data", "{}"))
        except (ValueError, TypeError):
            return Session(None, data=None, new=True, max_age=self.max_age)

        return Session(key, data=data, new=False, max_age=self.max_age)

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
        * **Non-empty session** -- data is written (or overwritten) and the
          cookie is set.
        """
        key = session.identity

        if key is None:
            if session.empty:
                return
            key = self._key_factory()
            self.save_cookie(response, key, max_age=session.max_age)
        else:
            if session.empty:
                self.save_cookie(response, "", max_age=session.max_age)
                await self._collection.document(key).delete()
                return
            self.save_cookie(response, key, max_age=session.max_age)

        data_str = self._encoder(self._get_session_data(session))
        doc_data: dict[str, Any] = {"data": data_str}

        if session.max_age is not None:
            doc_data["expire"] = _dt.datetime.now(
                tz=_dt.UTC,
            ) + _dt.timedelta(seconds=session.max_age)

        await self._collection.document(key).set(doc_data)

    @staticmethod
    def _is_expired(doc_dict: dict[str, Any]) -> bool:
        """Return ``True`` if the document's ``expire`` timestamp is in the past."""
        expire = doc_dict.get("expire")
        if not isinstance(expire, _dt.datetime):
            return False
        now = _dt.datetime.now(tz=_dt.UTC)
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=_dt.UTC)
        return now > expire
