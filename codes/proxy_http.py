# ==================================================
# file: proxy_http.py
# primary contributor: ali rida
# contributions:
# - http request parsing (parse_http_request)
# - host/port/path extraction (extract_target)
# - header rebuilding for forwarding (build_forward_request)
# - response header parsing (parse_response_headers)
# - cache ttl computation from response headers (cache_ttl_from_response)
# team support:
# - assil halawi (integration in proxy_server.py forwarding path)
# - reina harake (cache ttl result consumed by caching layer)
# ==================================================

from __future__ import annotations

import datetime
import email.utils
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class ParsedRequest:
    # ali rida: structured representation of a parsed http request
    method: str           # http method (GET, POST, CONNECT, ...) normalized to uppercase
    raw_target: str       # raw request target exactly as sent by the client
    http_version: str     # http version string (e.g. HTTP/1.1)
    host: str             # destination hostname extracted from url or Host header
    port: int             # destination port (default 80 for http, 443 for https/connect)
    path: str             # origin-form path forwarded to the server (e.g. /index.html?q=1)
    headers: dict[str, str]  # lowercase header dict (key → value)
    body: bytes           # raw body bytes (for POST etc.)
    full_url: str         # absolute url used as a stable cache key and for logging


def _split_header_lines(header_bytes: bytes) -> list[str]:
    # ali rida: decode as ISO-8859-1 which handles all 0-255 byte values safely
    text = header_bytes.decode("iso-8859-1", errors="replace")
    return text.split("\r\n")


def parse_http_request(raw: bytes) -> ParsedRequest:
    """
    ali rida: parse a single HTTP/1.x request from raw bytes.
    Supports absolute-form URLs (proxy mode), origin-form paths, and CONNECT.
    """
    # split raw bytes at the blank line separating headers from body
    header_part, body = split_headers_and_body(raw)
    lines = _split_header_lines(header_part)

    if not lines or not lines[0].strip():
        raise ValueError("Empty request line")

    parts = lines[0].split()
    if len(parts) != 3:
        raise ValueError("Invalid request line")

    method, raw_target, http_version = parts
    headers = parse_headers(lines[1:])

    # ali rida: decide destination host/port and the path to forward
    host, port, path, full_url = extract_target(method, raw_target, headers)

    return ParsedRequest(
        method=method.upper(),
        raw_target=raw_target,
        http_version=http_version,
        host=host,
        port=port,
        path=path,
        headers=headers,
        body=body,
        full_url=full_url,
    )


def split_headers_and_body(raw: bytes) -> tuple[bytes, bytes]:
    # ali rida: http uses \r\n\r\n as the separator between headers and body
    marker = b"\r\n\r\n"
    idx = raw.find(marker)
    if idx == -1:
        return raw, b""
    return raw[:idx], raw[idx + len(marker):]


def parse_headers(header_lines: list[str]) -> dict[str, str]:
    # ali rida: build a lowercase dict from raw header lines (key: value pairs)
    headers: dict[str, str] = {}
    for line in header_lines:
        if not line:
            continue
        if ":" not in line:
            # skip malformed lines — best-effort parsing
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def extract_target(
    method: str, raw_target: str, headers: dict[str, str]
) -> tuple[str, int, str, str]:
    """
    ali rida: determine (host, port, path, full_url) from the request line and headers.

    Three cases:
    1. CONNECT  — target is host:port, used for https tunneling
    2. Absolute-form URL — target starts with http:// or https://
    3. Origin-form path — use Host header to find the server
    """
    method = method.upper()

    if method == "CONNECT":
        # ali rida: CONNECT target is always "host:port"
        host_port = raw_target
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443
        path = raw_target
        full_url = f"https://{host}:{port}"
        return host, port, path, full_url

    if raw_target.startswith("http://") or raw_target.startswith("https://"):
        # ali rida: proxy clients send absolute-form URLs in the request line
        u = urlsplit(raw_target)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        full_url = raw_target
        return host, port, path, full_url

    # ali rida: origin-form — rely on the Host header for the server address
    host_header = headers.get("host", "")
    if not host_header:
        raise ValueError("Missing Host header")

    if ":" in host_header:
        host, port_str = host_header.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_header
        port = 80

    path = raw_target if raw_target.startswith("/") else "/" + raw_target
    full_url = f"http://{host}:{port}{path}"
    return host, port, path, full_url


def build_forward_request(req: ParsedRequest) -> bytes:
    """
    ali rida: rebuild the client request in origin-form for forwarding to the target server.
    Sets the Host header, removes hop-by-hop headers, and forces Connection: close.
    """
    headers = dict(req.headers)

    # set host header to exactly what we are connecting to
    headers["host"] = (
        f"{req.host}:{req.port}" if req.port not in (80, 443) else req.host
    )

    # ali rida: these headers are only meaningful for the current TCP connection
    # and must not be forwarded to the next hop (RFC 7230 §6.1)
    headers.pop("proxy-connection", None)
    headers.pop("connection", None)
    headers.pop("keep-alive", None)
    headers.pop("te", None)
    headers.pop("trailers", None)
    headers.pop("transfer-encoding", None)
    headers.pop("upgrade", None)

    # force close so reading the response is simple (server closes socket when done)
    headers["connection"] = "close"

    request_line = f"{req.method} {req.path} {req.http_version}\r\n"
    header_lines = "".join(f"{k.title()}: {v}\r\n" for k, v in headers.items())
    return (request_line + header_lines + "\r\n").encode("iso-8859-1") + req.body


def parse_response_headers(response: bytes) -> tuple[int | None, dict[str, str]]:
    """ali rida: extract status code and headers from a raw HTTP response."""
    header_part, _body = split_headers_and_body(response)
    lines = _split_header_lines(header_part)
    if not lines:
        return None, {}

    status_code = None
    first = lines[0].split()
    if len(first) >= 2 and first[1].isdigit():
        status_code = int(first[1])

    headers = parse_headers(lines[1:])
    return status_code, headers


def cache_ttl_from_response(headers: dict[str, str], default_ttl: int) -> int:
    """
    reina harake: compute how many seconds a response should be cached.

    Priority:
    1. Cache-Control: no-store or private → do not cache (return 0)
    2. Cache-Control: max-age=N → use N seconds
    3. Expires header → compute seconds until that time
    4. Fallback to default_ttl
    """
    cc = headers.get("cache-control", "").lower()

    if "no-store" in cc or "private" in cc:
        return 0

    # reina harake: look for max-age directive in cache-control header
    for part in cc.split(","):
        part = part.strip()
        if part.startswith("max-age="):
            try:
                return max(0, int(part.split("=", 1)[1].strip()))
            except ValueError:
                break

    # reina harake: fall back to Expires header (older HTTP/1.0 mechanism)
    exp = headers.get("expires")
    if exp:
        dt = _parse_http_date(exp)
        if dt:
            seconds = int((dt - datetime.datetime.utcnow()).total_seconds())
            return max(0, seconds)

    return default_ttl


def _parse_http_date(value: str) -> datetime.datetime | None:
    # reina harake: parse standard http date formats (RFC 5322 / RFC 7231)
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            # normalize to naive utc for comparison with utcnow()
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None
