# ==================================================
# file: proxy_state.py
# primary contributor: reina harake
# contributions:
# - live proxy state management (ProxyState)
# - thread-safe counters (requests, errors, blocked, tunnels, cache hits/misses)
# - active and recent connection tracking
# - snapshot() for admin ui consumption
# team support:
# - assil halawi (state.inc_* and conn_* calls in proxy_server.py)
# - ali rida (state.inc_tunnel, state.inc_blocked calls)
# ==================================================

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveConnection:
    # reina harake: immutable snapshot of a connection currently being handled
    client_ip: str
    client_port: int
    started_at: float               # unix timestamp of connection acceptance
    method: str | None = None       # filled after request is parsed
    target_host: str | None = None
    target_port: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class RecentConnection(ActiveConnection):
    # reina harake: same as ActiveConnection but with an end timestamp
    ended_at: float = 0.0


class ProxyState:
    """
    reina harake: thread-safe shared state for the proxy server.
    All public methods acquire self._lock so they are safe to call from any thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()

        # active connections keyed by a unique string (ip:port:timestamp)
        self._active: dict[str, ActiveConnection] = {}
        # ring buffer of the last 50 completed connections for the admin ui
        self._recent: deque[RecentConnection] = deque(maxlen=50)

        # aggregate counters
        self._total_requests = 0
        self._total_errors = 0
        self._total_blocked = 0
        self._total_tunnels = 0

        # cache performance counters
        self._cache_hits = 0
        self._cache_misses = 0

        # traffic counters (bytes)
        self._bytes_to_clients = 0
        self._bytes_from_clients = 0

    def started_at(self) -> float:
        return self._started_at

    # ── connection lifecycle ──────────────────────────────────────────────────

    def conn_add(self, client_ip: str, client_port: int) -> str:
        """reina harake: register a new connection and return its unique key."""
        key = f"{client_ip}:{client_port}:{time.time()}"
        with self._lock:
            self._active[key] = ActiveConnection(
                client_ip=client_ip,
                client_port=client_port,
                started_at=time.time(),
            )
        return key

    def conn_update(
        self,
        key: str,
        *,
        method: str | None = None,
        target_host: str | None = None,
        target_port: int | None = None,
        url: str | None = None,
    ) -> None:
        """reina harake: update parsed request details on an existing connection record."""
        with self._lock:
            cur = self._active.get(key)
            if not cur:
                # connection was already removed (race condition) — ignore
                return
            # frozen dataclass: replace with a new instance that has the updated fields
            self._active[key] = ActiveConnection(
                client_ip=cur.client_ip,
                client_port=cur.client_port,
                started_at=cur.started_at,
                method=method if method is not None else cur.method,
                target_host=target_host if target_host is not None else cur.target_host,
                target_port=target_port if target_port is not None else cur.target_port,
                url=url if url is not None else cur.url,
            )

    def conn_remove(self, key: str) -> None:
        """reina harake: move a connection from active to recent history."""
        with self._lock:
            cur = self._active.pop(key, None)
            if cur:
                self._recent.appendleft(
                    RecentConnection(
                        client_ip=cur.client_ip,
                        client_port=cur.client_port,
                        started_at=cur.started_at,
                        method=cur.method,
                        target_host=cur.target_host,
                        target_port=cur.target_port,
                        url=cur.url,
                        ended_at=time.time(),
                    )
                )

    # ── counters ──────────────────────────────────────────────────────────────

    def inc_request(self) -> None:
        with self._lock:
            self._total_requests += 1

    def inc_error(self) -> None:
        with self._lock:
            self._total_errors += 1

    def inc_blocked(self) -> None:
        with self._lock:
            self._total_blocked += 1

    def inc_tunnel(self) -> None:
        with self._lock:
            self._total_tunnels += 1

    def inc_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def inc_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def add_bytes_to_clients(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._bytes_to_clients += n

    def add_bytes_from_clients(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._bytes_from_clients += n

    # ── snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """
        reina harake: return a consistent dictionary of all state fields.
        Used by the admin ui to render every page.
        Single lock region so the snapshot is always self-consistent.
        """
        with self._lock:
            return {
                "started_at": self._started_at,
                "uptime_seconds": int(time.time() - self._started_at),
                "active_connections": list(self._active.values()),
                "active_count": len(self._active),
                "recent_connections": list(self._recent),
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "total_blocked": self._total_blocked,
                "total_tunnels": self._total_tunnels,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_hit_rate": (
                    (self._cache_hits / (self._cache_hits + self._cache_misses))
                    if (self._cache_hits + self._cache_misses) > 0
                    else None
                ),
                "bytes_to_clients": self._bytes_to_clients,
                "bytes_from_clients": self._bytes_from_clients,
            }
