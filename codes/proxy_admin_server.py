# ==================================================
# file: proxy_admin_server.py
# primary contributor: reina harake
# contributions:
# - admin web interface (all pages)
# - log tail display
# - cache view and management ui
# - filter management ui (blacklist + whitelist)
# - active/recent connection display
# team support:
# - assil halawi (integration with proxy_server.py startup)
# - ali rida (filter backend functions consumed here)
# ==================================================

from __future__ import annotations

import html
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from proxy_cache import SimpleCache
from proxy_filters import (
    add_to_blacklist,
    add_to_whitelist,
    get_filters_snapshot,
    remove_from_blacklist,
    remove_from_whitelist,
    set_whitelist_mode,
)
from proxy_state import ProxyState


def start_admin_server(
    *,
    host: str,
    port: int,
    state: ProxyState,
    cache: SimpleCache,
    log_path: str = "proxy.log",
) -> ThreadingHTTPServer:
    """Create and return the admin ThreadingHTTPServer (caller starts it)."""
    # build a request handler class that closes over state/cache/log_path
    Handler = _make_handler(state=state, cache=cache, log_path=log_path)
    httpd: ThreadingHTTPServer = ThreadingHTTPServer((host, port), Handler)
    return httpd


def _make_handler(*, state: ProxyState, cache: SimpleCache, log_path: str):
    """Factory that returns an AdminHandler class with the proxy objects baked in."""

    class AdminHandler(BaseHTTPRequestHandler):

        def do_GET(self) -> None:
            # parse the url path and query string
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/":
                self._send_html(_page_dashboard(state=state, cache=cache))
                return
            if path == "/logs":
                n = _int(qs.get("n", ["200"])[0], 200)
                self._send_html(_page_logs(log_path=log_path, tail_lines=n))
                return
            if path == "/cache":
                self._send_html(_page_cache(cache=cache))
                return
            if path == "/filters":
                self._send_html(_page_filters())
                return
            if path == "/active":
                self._send_html(_page_active(state=state))
                return

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            # read url-encoded form body
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
            form = parse_qs(body.decode("utf-8", errors="replace"))

            # ── cache actions ──────────────────────────────────────────────────
            if path == "/cache/clear":
                cache.clear()
                cache.cleanup()
                return self._redirect("/cache")

            if path == "/cache/delete":
                key = (form.get("key", [""])[0] or "").strip()
                if key:
                    cache.delete(key)
                cache.cleanup()
                return self._redirect("/cache")

            # ── blacklist actions ──────────────────────────────────────────────
            if path == "/filters/blacklist/add":
                domain = (form.get("domain", [""])[0] or "").strip()
                add_to_blacklist(domain)
                return self._redirect("/filters")

            if path == "/filters/blacklist/remove":
                domain = (form.get("domain", [""])[0] or "").strip()
                remove_from_blacklist(domain)
                return self._redirect("/filters")

            # ── whitelist actions ──────────────────────────────────────────────
            if path == "/filters/whitelist/add":
                domain = (form.get("domain", [""])[0] or "").strip()
                add_to_whitelist(domain)
                return self._redirect("/filters")

            if path == "/filters/whitelist/remove":
                domain = (form.get("domain", [""])[0] or "").strip()
                remove_from_whitelist(domain)
                return self._redirect("/filters")

            # ── mode toggle ────────────────────────────────────────────────────
            if path == "/filters/mode":
                # the form sends mode=whitelist or mode=blacklist
                mode_val = (form.get("mode", ["blacklist"])[0] or "blacklist").strip().lower()
                set_whitelist_mode(mode_val == "whitelist")
                return self._redirect("/filters")

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

        def _send_html(self, body: str, *, status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to: str) -> None:
            # 303 See Other — standard redirect after POST so browser doesn't re-submit on refresh
            self.send_response(303)
            self.send_header("Location", to)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            # suppress default access log lines so the terminal stays clean during demos
            return

    return AdminHandler


# ── shared page layout ─────────────────────────────────────────────────────────

def _page_layout(title: str, content: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #0b1220; color: #e6eefc; }}
    a {{ color: #93c5fd; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav a {{ margin-right: 12px; }}
    .card {{ background: #111b33; border: 1px solid #223158; border-radius: 12px; padding: 16px; margin: 16px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #223158; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: #c7d2fe; font-weight: 600; }}
    code {{ background: #0b1220; padding: 2px 6px; border-radius: 6px; }}
    input[type=text] {{ width: 320px; max-width: 95%; padding: 8px; border-radius: 8px; border: 1px solid #223158; background: #0b1220; color: #e6eefc; }}
    button {{ padding: 8px 12px; border-radius: 10px; border: 1px solid #223158; background: #1b2a52; color: #e6eefc; cursor: pointer; }}
    button:hover {{ background: #223158; }}
    button.danger {{ border-color: #7f1d1d; background: #450a0a; color: #fca5a5; }}
    button.danger:hover {{ background: #7f1d1d; }}
    button.active-mode {{ border-color: #1d4ed8; background: #1e3a8a; color: #bfdbfe; font-weight: 600; }}
    .muted {{ color: #a7b0c8; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #223158; background: #0b1220; }}
    .badge-bl {{ display: inline-block; padding: 2px 10px; border-radius: 999px; background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; font-size: 12px; font-weight: 600; }}
    .badge-wl {{ display: inline-block; padding: 2px 10px; border-radius: 999px; background: #052e16; color: #86efac; border: 1px solid #166534; font-size: 12px; font-weight: 600; }}
    .badge-mode-bl {{ display: inline-block; padding: 4px 14px; border-radius: 999px; background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; font-weight: 600; }}
    .badge-mode-wl {{ display: inline-block; padding: 4px 14px; border-radius: 999px; background: #052e16; color: #86efac; border: 1px solid #166534; font-weight: 600; }}
    h3 {{ margin: 16px 0 8px; color: #c7d2fe; font-size: 15px; }}
    hr {{ border: none; border-top: 1px solid #223158; margin: 16px 0; }}
  </style>
</head>
<body>
  <div class="nav card">
    <b>Proxy Admin</b>
    <span class="muted">|</span>
    <a href="/">Dashboard</a>
    <a href="/active">Active</a>
    <a href="/cache">Cache</a>
    <a href="/filters">Filters</a>
    <a href="/logs">Logs</a>
  </div>
  {content}
</body>
</html>"""


# ── dashboard ──────────────────────────────────────────────────────────────────

def _page_dashboard(*, state: ProxyState, cache: SimpleCache) -> str:
    snap = state.snapshot()
    entries = cache.list_entries()

    hit_rate = snap["cache_hit_rate"]
    hit_rate_text = f"{hit_rate*100:.1f}%" if hit_rate is not None else "N/A"

    uptime_s = snap["uptime_seconds"]
    uptime_text = f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m {uptime_s % 60}s"

    # reina harake: dashboard stat cards
    content = f"""
  <div class="card">
    <h2 style="margin-top:0">Stats</h2>
    <div class="row">
      <div class="pill">Uptime: <b>{uptime_text}</b></div>
      <div class="pill">Active connections: <b>{snap["active_count"]}</b></div>
      <div class="pill">Total requests: <b>{snap["total_requests"]}</b></div>
      <div class="pill">Blocked: <b>{snap["total_blocked"]}</b></div>
      <div class="pill">Tunnels (CONNECT): <b>{snap["total_tunnels"]}</b></div>
      <div class="pill">Errors: <b>{snap["total_errors"]}</b></div>
    </div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Cache</h2>
    <div class="row">
      <div class="pill">Entries: <b>{len(entries)}</b></div>
      <div class="pill">Hits: <b>{snap["cache_hits"]}</b></div>
      <div class="pill">Misses: <b>{snap["cache_misses"]}</b></div>
      <div class="pill">Hit rate: <b>{hit_rate_text}</b></div>
    </div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Traffic</h2>
    <div class="row">
      <div class="pill">Bytes from clients: <b>{snap["bytes_from_clients"]}</b></div>
      <div class="pill">Bytes to clients: <b>{snap["bytes_to_clients"]}</b></div>
    </div>
  </div>
"""
    return _page_layout("Proxy Admin - Dashboard", content)


# ── active connections ─────────────────────────────────────────────────────────

def _page_active(*, state: ProxyState) -> str:
    snap = state.snapshot()
    rows = []
    now = time.time()

    for c in snap["active_connections"]:
        age = int(now - c.started_at)
        rows.append(
            "<tr>"
            f"<td>{html.escape(c.client_ip)}:{c.client_port}</td>"
            f"<td>{html.escape(c.method or '')}</td>"
            f"<td>{html.escape((c.target_host or '') + ((':' + str(c.target_port)) if c.target_port else ''))}</td>"
            f"<td><code>{html.escape(c.url or '')}</code></td>"
            f"<td>{age}s</td>"
            "</tr>"
        )

    table = (
        "<table><thead><tr><th>Client</th><th>Method</th><th>Target</th><th>URL</th><th>Age</th></tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='5' class='muted'>No active connections.</td></tr>")
        + "</tbody></table>"
    )

    recent_rows = []
    for c in snap.get("recent_connections", [])[:20]:
        age = int(now - c.started_at)
        ago = int(now - c.ended_at) if getattr(c, "ended_at", None) else 0
        recent_rows.append(
            "<tr>"
            f"<td>{html.escape(c.client_ip)}:{c.client_port}</td>"
            f"<td>{html.escape(c.method or '')}</td>"
            f"<td>{html.escape((c.target_host or '') + ((':' + str(c.target_port)) if c.target_port else ''))}</td>"
            f"<td><code>{html.escape(c.url or '')}</code></td>"
            f"<td>{age}s</td>"
            f"<td>{ago}s ago</td>"
            "</tr>"
        )

    recent_table = (
        "<table><thead><tr><th>Client</th><th>Method</th><th>Target</th><th>URL</th><th>Duration</th><th>Ended</th></tr></thead><tbody>"
        + (
            "".join(recent_rows)
            if recent_rows
            else "<tr><td colspan='6' class='muted'>No recent connections yet.</td></tr>"
        )
        + "</tbody></table>"
    )

    content = f"""
  <div class="card">
    <h2 style="margin-top:0">Active connections</h2>
    {table}
  </div>
  <div class="card">
    <h2 style="margin-top:0">Recent connections (last 50)</h2>
    {recent_table}
  </div>
"""
    return _page_layout("Proxy Admin - Active", content)


# ── cache page ─────────────────────────────────────────────────────────────────

def _page_cache(*, cache: SimpleCache) -> str:
    now = time.time()
    entries = cache.list_entries()
    rows = []

    # reina harake: cache entry table with delete buttons
    for key, expires_at, size_bytes in entries:
        ttl = max(0, int(expires_at - now))
        safe_key = html.escape(key)
        safe_key_attr = html.escape(key, quote=True)
        rows.append(
            "<tr>"
            f"<td style='word-break:break-all'><code>{safe_key}</code></td>"
            f"<td>{ttl}s</td>"
            f"<td>{size_bytes}</td>"
            "<td>"
            "<form method='POST' action='/cache/delete' style='margin:0'>"
            f"<input type='hidden' name='key' value='{safe_key_attr}'/>"
            "<button type='submit'>Delete</button>"
            "</form>"
            "</td>"
            "</tr>"
        )

    table = (
        "<table><thead><tr><th>Key (URL)</th><th>TTL</th><th>Size (bytes)</th><th>Action</th></tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='4' class='muted'>Cache is empty.</td></tr>")
        + "</tbody></table>"
    )

    content = f"""
  <div class="card">
    <h2 style="margin-top:0">Cache entries</h2>
    <form method="POST" action="/cache/clear" style="margin: 0 0 12px 0">
      <button type="submit">Clear cache</button>
    </form>
    {table}
  </div>
"""
    return _page_layout("Proxy Admin - Cache", content)


# ── filters page ──────────────────────────────────────────────────────────────

def _page_filters() -> str:
    """
    Renders the full filters management page.

    Sections:
    1. Current mode indicator + toggle button
    2. Blacklist — view, add, remove
    3. Whitelist — view, add, remove
    """
    snap = get_filters_snapshot()
    is_wl_mode = snap["whitelist_mode"]

    # reina harake: build mode indicator and toggle form
    if is_wl_mode:
        mode_badge = "<span class='badge-mode-wl'>Whitelist Mode</span>"
        mode_description = "Only domains in the whitelist are allowed. Everything else is blocked."
        toggle_label = "Switch to Blacklist Mode"
        toggle_value = "blacklist"
    else:
        mode_badge = "<span class='badge-mode-bl'>Blacklist Mode</span>"
        mode_description = "Domains in the blacklist are blocked. Everything else is allowed."
        toggle_label = "Switch to Whitelist Mode"
        toggle_value = "whitelist"

    mode_section = f"""
    <h2 style="margin-top:0">Filtering Mode</h2>
    <div class="row" style="margin-bottom:12px">
      <div>Current mode: {mode_badge}</div>
      <span class="muted" style="font-size:13px">{mode_description}</span>
    </div>
    <form method="POST" action="/filters/mode" style="margin:0">
      <input type="hidden" name="mode" value="{toggle_value}"/>
      <button type="submit">{toggle_label}</button>
    </form>
"""

    # ali rida: blacklist entries and management forms
    bl_items = snap["blacklist"]
    bl_list = (
        "".join(
            f"<li style='display:flex;align-items:center;gap:8px;padding:4px 0'>"
            f"<code>{html.escape(d)}</code>"
            f"<form method='POST' action='/filters/blacklist/remove' style='margin:0'>"
            f"<input type='hidden' name='domain' value='{html.escape(d, quote=True)}'/>"
            f"<button type='submit' class='danger' style='padding:3px 8px;font-size:12px'>Remove</button>"
            f"</form>"
            f"</li>"
            for d in bl_items
        )
        or "<li class='muted'>(empty)</li>"
    )

    blacklist_section = f"""
    <hr/>
    <h3><span class="badge-bl">Blacklist</span> &nbsp; Domains that are blocked in blacklist mode</h3>
    <div class="row" style="margin-bottom:8px">
      <form method="POST" action="/filters/blacklist/add" style="margin:0;display:flex;gap:8px">
        <input type="text" name="domain" placeholder="example.com"/>
        <button type="submit">Add</button>
      </form>
    </div>
    <ul style="list-style:none;padding:0;margin:0">{bl_list}</ul>
"""

    # ali rida: whitelist entries and management forms
    wl_items = snap["whitelist"]
    wl_list = (
        "".join(
            f"<li style='display:flex;align-items:center;gap:8px;padding:4px 0'>"
            f"<code>{html.escape(d)}</code>"
            f"<form method='POST' action='/filters/whitelist/remove' style='margin:0'>"
            f"<input type='hidden' name='domain' value='{html.escape(d, quote=True)}'/>"
            f"<button type='submit' class='danger' style='padding:3px 8px;font-size:12px'>Remove</button>"
            f"</form>"
            f"</li>"
            for d in wl_items
        )
        or "<li class='muted'>(empty)</li>"
    )

    whitelist_section = f"""
    <hr/>
    <h3><span class="badge-wl">Whitelist</span> &nbsp; Domains that are allowed in whitelist mode</h3>
    <div class="row" style="margin-bottom:8px">
      <form method="POST" action="/filters/whitelist/add" style="margin:0;display:flex;gap:8px">
        <input type="text" name="domain" placeholder="httpforever.com"/>
        <button type="submit">Add</button>
      </form>
    </div>
    <ul style="list-style:none;padding:0;margin:0">{wl_list}</ul>
"""

    content = f"""
  <div class="card">
    {mode_section}
    {blacklist_section}
    {whitelist_section}
  </div>
"""
    return _page_layout("Proxy Admin - Filters", content)


# ── logs page ──────────────────────────────────────────────────────────────────

def _page_logs(*, log_path: str, tail_lines: int) -> str:
    # reina harake: tail the log file and render it in a scrollable pre block
    lines = _tail_text_file(log_path, tail_lines)
    safe = html.escape("".join(lines))
    file_info = html.escape(os.path.abspath(log_path))

    content = f"""
  <div class="card">
    <h2 style="margin-top:0">Logs</h2>
    <p class="muted">File: <code>{file_info}</code></p>
    <pre style="white-space:pre-wrap; background:#0b1220; border:1px solid #223158; padding:12px; border-radius:12px; max-height: 70vh; overflow:auto;">{safe}</pre>
  </div>
"""
    return _page_layout("Proxy Admin - Logs", content)


# ── helpers ────────────────────────────────────────────────────────────────────

def _tail_text_file(path: str, n_lines: int) -> list[str]:
    """Read the last n_lines from a text file. Returns a list of line strings."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-max(1, n_lines):]
    except FileNotFoundError:
        return ["(log file not found yet)\n"]
    except Exception as e:
        return [f"(could not read log file: {e})\n"]


def _int(value: str, default: int) -> int:
    """Safe int parse with fallback."""
    try:
        return int(value)
    except Exception:
        return default
