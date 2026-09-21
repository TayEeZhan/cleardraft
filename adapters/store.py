"""Key-value storage for accounts: users, sessions, and per-user mailboxes.

OWNER: whoever built the accounts API (api/_accounts.py). This is the ONLY
module that talks to Redis (or an in-memory stand-in); every other module
speaks plain get/set/delete/incr, the same discipline adapters/eml.py and
adapters/model.py already use for their own external boundary.

Two backends:
  - UpstashStore: Upstash Redis REST API over urllib (stdlib only - no new
    pip dependency). One HTTP POST per command, command array as JSON body,
    e.g. ["SET", "k", "v", "EX", "60"].
  - MemoryStore: a dict with per-key expiry, for tests and local dev.

get_store() decides which one to use, once, and caches the instance so every
request in the same process shares it (an in-memory store that reset on
every call would make sessions vanish request to request).

Values are opaque strings to this module - it never inspects, logs, or
otherwise looks inside what callers store here.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable


class StoreError(RuntimeError):
    """The backing store could not complete a request."""


@runtime_checkable
class Store(Protocol):
    def get(self, key: str) -> "str | None": ...

    def set(self, key: str, value: str, ex_seconds: "int | None" = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def incr(self, key: str, ex_seconds: "int | None" = None) -> int:
        """Increment key by 1 (starting from 0) and return the new value.

        When ex_seconds is given, an expiry is (re)applied on the SAME call
        that creates or bumps the counter, so a rate-limit window is set
        atomically with its first increment rather than in a second
        round-trip that could race or be skipped.
        """
        ...


# ---------------------------------------------------------------------------
# Upstash Redis REST API
# ---------------------------------------------------------------------------
class UpstashStore:
    """Upstash Redis via its REST API, using only the standard library.

    Each call is one POST to the base URL with a JSON array command body
    (e.g. ["SET", "k", "v", "EX", "60"]) and an
    `Authorization: Bearer <token>` header. The response is JSON:
    {"result": ...} on success or {"error": "..."} on failure.
    """

    _TIMEOUT = 5.0

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token

    def _command(self, *parts: "str | int") -> object:
        body = json.dumps([str(p) for p in parts]).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise StoreError(f"upstash request failed: {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise StoreError(f"upstash returned unparseable response: {exc}") from exc

        if isinstance(payload, dict) and payload.get("error"):
            raise StoreError(f"upstash error: {payload['error']}")
        if isinstance(payload, dict):
            return payload.get("result")
        return payload

    def get(self, key: str) -> "str | None":
        result = self._command("GET", key)
        return None if result is None else str(result)

    def set(self, key: str, value: str, ex_seconds: "int | None" = None) -> None:
        if ex_seconds is not None:
            self._command("SET", key, value, "EX", int(ex_seconds))
        else:
            self._command("SET", key, value)

    def delete(self, key: str) -> None:
        self._command("DEL", key)

    def incr(self, key: str, ex_seconds: "int | None" = None) -> int:
        result = self._command("INCR", key)
        new_value = int(result)
        if ex_seconds is not None:
            self._command("EXPIRE", key, int(ex_seconds))
        return new_value


# ---------------------------------------------------------------------------
# In-memory store: tests and local dev
# ---------------------------------------------------------------------------
class MemoryStore:
    """A dict with expiry. Not shared across processes - tests/local only."""

    def __init__(self) -> None:
        # key -> (value, expires_at_monotonic | None)
        self._data: "dict[str, tuple[str, float | None]]" = {}

    def _expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _value, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            del self._data[key]
            return True
        return False

    def get(self, key: str) -> "str | None":
        if self._expired(key):
            return None
        return self._data[key][0]

    def set(self, key: str, value: str, ex_seconds: "int | None" = None) -> None:
        expires_at = time.monotonic() + ex_seconds if ex_seconds is not None else None
        self._data[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def incr(self, key: str, ex_seconds: "int | None" = None) -> int:
        current = 0 if self._expired(key) else int(self._data[key][0])
        current += 1
        # Preserve an existing expiry unless the caller supplies a new one -
        # mirrors Redis's INCR (never touches TTL) plus an optional EXPIRE.
        if ex_seconds is not None:
            self.set(key, str(current), ex_seconds=ex_seconds)
        else:
            _prev_value, expires_at = self._data.get(key, (None, None))
            remaining = None
            if expires_at is not None:
                remaining = max(0.0, expires_at - time.monotonic())
            self.set(key, str(current), ex_seconds=remaining)
        return current


_store_instance: "Store | None" = None
_store_resolved = False


def get_store() -> "Store | None":
    """Return the process-wide store, resolving and caching it once.

    Preference order:
      1. UpstashStore, when KV_REST_API_URL + KV_REST_API_TOKEN are set (what
         the Vercel Upstash integration injects), falling back to
         UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN.
      2. MemoryStore, when CLEARDRAFT_STORE=memory (tests/local).
      3. None - accounts are unavailable; callers must return 503.
    """
    global _store_instance, _store_resolved
    if _store_resolved:
        return _store_instance

    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        _store_instance = UpstashStore(url, token)
    elif os.environ.get("CLEARDRAFT_STORE", "").strip() == "memory":
        _store_instance = MemoryStore()
    else:
        _store_instance = None

    _store_resolved = True
    return _store_instance


def reset_store_cache() -> None:
    """Test-only hook: forget the cached store so get_store() re-resolves.

    Needed because tests flip CLEARDRAFT_STORE / the Upstash env vars between
    cases and otherwise the first resolution would stick for the whole run.
    """
    global _store_instance, _store_resolved
    _store_instance = None
    _store_resolved = False


__all__ = ["Store", "StoreError", "UpstashStore", "MemoryStore", "get_store", "reset_store_cache"]
