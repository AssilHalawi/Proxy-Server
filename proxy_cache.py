# ==================================================
# file: proxy_cache.py
# primary contributor: reina harake
# contributions:
# - in-memory response cache (SimpleCache)
# - ttl-based expiration
# - cache entry management (get, set, delete, clear, cleanup)
# - list_entries for admin ui display
# team support:
# - assil halawi (cache.get/set calls in proxy_server.py)
# - ali rida (cache_ttl_from_response result feeds into cache.set ttl)
# ==================================================

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CacheEntry:
    # reina harake: stores the full raw http response bytes plus an expiry timestamp
    response_bytes: bytes   # full raw response (status line + headers + body)
    expires_at: float       # unix timestamp when this entry becomes invalid


class SimpleCache:
    """
    reina harake: small in-memory response cache.

    Keys are absolute URLs (strings).
    Values are complete raw HTTP response bytes.
    Entries expire after a TTL derived from response headers (or a default).
    """

    def __init__(self) -> None:
        # reina harake: internal store mapping url → cache entry
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> bytes | None:
        """Return cached response bytes for key, or None on miss/expiry."""
        entry = self._store.get(key)
        if not entry:
            # cache miss
            return None

        if time.time() >= entry.expires_at:
            # reina harake: entry has expired — remove it and treat as a miss
            self._store.pop(key, None)
            return None

        # cache hit
        return entry.response_bytes

    def set(self, key: str, response_bytes: bytes, ttl_seconds: int) -> None:
        """Store response_bytes under key with the given TTL."""
        # reina harake: ttl <= 0 means "do not cache" (e.g. Cache-Control: no-store)
        if ttl_seconds <= 0:
            return
        self._store[key] = CacheEntry(
            response_bytes=response_bytes,
            expires_at=time.time() + ttl_seconds,
        )

    def cleanup(self) -> None:
        """Remove all expired entries. Safe to call frequently."""
        # reina harake: gather keys first so we don't mutate the dict while iterating
        now = time.time()
        expired_keys = [k for k, v in self._store.items() if now >= v.expires_at]
        for k in expired_keys:
            self._store.pop(k, None)

    def list_entries(self) -> list[tuple[str, float, int]]:
        """
        reina harake: return a snapshot of (key, expires_at, size_bytes) for all
        non-expired entries, sorted by soonest-to-expire first.
        Used by the admin interface.
        """
        now = time.time()
        out: list[tuple[str, float, int]] = []
        for k, v in list(self._store.items()):
            if now >= v.expires_at:
                continue
            out.append((k, v.expires_at, len(v.response_bytes)))
        out.sort(key=lambda t: t[1])
        return out

    def delete(self, key: str) -> bool:
        """Remove one entry by key. Returns True if the key existed."""
        # reina harake: used by the admin ui "Delete" button per cache entry
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Remove all entries. Used by the admin ui "Clear cache" button."""
        # reina harake: full cache flush
        self._store.clear()
