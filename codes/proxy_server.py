# ==================================================
# file: proxy_server.py
# primary contributor: assil halawi
# contributions:
# - core proxy functionality (server loop, client handler)
# - socket programming (listen/accept/connect)
# - multithreading (one thread per client connection)
# - http forwarding and response relaying
# - blacklist/whitelist enforcement
# - cache hit/miss handling
# team support:
# - reina harake (logging calls, cache integration, admin server startup)
# - ali rida (request parsing integration, https tunnel integration)
# ==================================================

import argparse
import socket
import threading
import time
from typing import Tuple

from proxy_cache import SimpleCache
from proxy_admin_server import start_admin_server
from proxy_filters import is_blocked
from proxy_https import tunnel_connect
from proxy_http import (
    build_forward_request,
    cache_ttl_from_response,
    parse_http_request,
    parse_response_headers,
)
from proxy_logging import build_logger
from proxy_state import ProxyState

# default cache ttl when the server response gives us no caching headers
DEFAULT_CACHE_TTL_SECONDS = 60
# bytes read per recv() call (client socket and server socket)
BUFFER_SIZE = 4096

# single shared logger — writes to proxy.log
logger = build_logger("proxy.log")
# in-memory response cache shared across all threads
cache = SimpleCache()
# live stats shared across all threads (used by admin ui)
state = ProxyState()


def main() -> None:
    # assil halawi: cli argument parsing so ports can be changed without editing code
    parser = argparse.ArgumentParser(description="Simple caching proxy server (HTTP + CONNECT tunnel).")
    parser.add_argument("--host", default="127.0.0.1", help="IP address to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8888, help="Port to listen on (default: 8888)")
    parser.add_argument("--admin-host", default="127.0.0.1", help="Admin UI bind IP (default: 127.0.0.1)")
    parser.add_argument("--admin-port", type=int, default=8890, help="Admin UI port (default: 8890)")
    parser.add_argument("--no-admin", action="store_true", help="Disable admin web interface")
    args = parser.parse_args()

    run_server(args.host, args.port, args.admin_host, args.admin_port, args.no_admin)


def run_server(bind_host: str, bind_port: int, admin_host: str, admin_port: int, no_admin: bool) -> None:
    # assil halawi: create the main proxy listening socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR lets us restart quickly without "address already in use"
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind_host, bind_port))
    # backlog of 50 is enough for a local demo/testing environment
    server.listen(50)

    print(f"Proxy listening on {bind_host}:{bind_port}")
    if not no_admin:
        # reina harake: start admin ui in a background daemon thread so it doesn't block the proxy
        admin_httpd = start_admin_server(
            host=admin_host, port=admin_port, state=state, cache=cache, log_path="proxy.log"
        )
        threading.Thread(target=admin_httpd.serve_forever, daemon=True).start()
        print(f"Admin UI available at http://{admin_host}:{admin_port}/")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            # assil halawi: accept blocks here until a client connects
            client_sock, client_addr = server.accept()
            # spawn a daemon thread for each client so the accept loop stays free
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        print("\nStopping proxy...")
    finally:
        server.close()


def handle_client(client_sock: socket.socket, client_addr: Tuple[str, int]) -> None:
    """
    assil halawi: main per-client handler.
    Runs in its own thread. Handles the full lifecycle:
    receive → parse → filter → cache check → forward → log.
    """
    client_ip, client_port = client_addr
    print("Received request from", client_ip, ":", client_port) 
    start_time = time.time()
    # register this connection in the live state so the admin ui can show it
    conn_key = state.conn_add(client_ip, client_port)

    try:
        # receive the raw http request bytes from the client
        raw_request = recv_http_message(client_sock)
        if not raw_request:
            # client closed the connection before sending anything
            return

        # ali rida: parse the raw bytes into a structured request object
        req = parse_http_request(raw_request)

        # update stats and the live connection record with parsed request details
        state.inc_request()
        state.add_bytes_from_clients(len(raw_request))
        state.conn_update(
            conn_key,
            method=req.method,
            target_host=req.host,
            target_port=req.port,
            url=req.full_url,
        )

        # ali rida: check blacklist/whitelist before making any outbound connection
        if is_blocked(req.host):
            send_forbidden(client_sock)
            state.inc_blocked()
            logger.info(
                f"BLOCKED | client={client_ip}:{client_port} | host={req.host}:{req.port} | "
                f"method={req.method} | url={req.full_url}"
            )
            return

        if req.method == "CONNECT":
            # ali rida: HTTPS tunnel — forward encrypted bytes without decrypting
            state.inc_tunnel()
            tunnel_connect(client_sock, client_addr, req.host, req.port)
            logger.info(
                f"TUNNEL | client={client_ip}:{client_port} | host={req.host}:{req.port} | "
                f"method=CONNECT | url={req.full_url} | duration_ms={int((time.time()-start_time)*1000)}"
            )
            return

        # reina harake: cache lookup (GET requests only)
        served_from_cache = False
        if req.method == "GET":
            cached = cache.get(req.full_url)
            if cached is not None:
                # cache hit — serve the stored response directly, skip the network
                state.inc_cache_hit()
                client_sock.sendall(cached)
                state.add_bytes_to_clients(len(cached))
                served_from_cache = True
                logger.info(
                    f"CACHE_HIT | client={client_ip}:{client_port} | host={req.host}:{req.port} | "
                    f"method={req.method} | url={req.full_url} | bytes={len(cached)} | "
                    f"duration_ms={int((time.time()-start_time)*1000)}"
                )
                return
            # cache miss — we will fetch from the target server below
            state.inc_cache_miss()

        # assil halawi: forward the request to the target server and read the full response
        response_bytes = forward_http_request(req)
        client_sock.sendall(response_bytes)
        state.add_bytes_to_clients(len(response_bytes))

        # parse status + headers so we can make caching decisions and log the status code
        status_code, resp_headers = parse_response_headers(response_bytes)

        if req.method == "GET":
            # reina harake: decide how long to cache this response
            ttl = cache_ttl_from_response(resp_headers, DEFAULT_CACHE_TTL_SECONDS)
            # only cache successful (200) responses to avoid caching error pages
            if ttl > 0 and status_code == 200:
                cache.set(req.full_url, response_bytes, ttl)
            # remove expired entries to keep memory usage bounded
            cache.cleanup()

        logger.info(
            f"OK | client={client_ip}:{client_port} | host={req.host}:{req.port} | "
            f"method={req.method} | url={req.full_url} | status={status_code} | "
            f"bytes={len(response_bytes)} | cache={served_from_cache} | "
            f"duration_ms={int((time.time()-start_time)*1000)}"
        )

    except Exception as e:
        # assil halawi: any unhandled exception gets logged with a full stack trace
        state.inc_error()
        logger.exception(f"ERROR | client={client_ip}:{client_port} | message={e}")
        try:
            send_bad_gateway(client_sock)
        except Exception:
            pass
    finally:
        # always remove the connection from active state and close the socket
        state.conn_remove(conn_key)
        try:
            client_sock.close()
        except Exception:
            pass


def recv_http_message(sock: socket.socket) -> bytes:
    """
    assil halawi: receive a single HTTP request from the client socket.
    Reads headers first, then reads body bytes according to Content-Length.
    """
    # timeout prevents a slow or broken client from hanging the thread forever
    sock.settimeout(10)
    data = b""

    # read until we have the full header section (terminated by \r\n\r\n)
    while b"\r\n\r\n" not in data and len(data) < 1024 * 1024:
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            # client closed the connection
            break
        data += chunk

    if not data:
        return b""

    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        # no end-of-headers marker found, return what we have (best effort)
        return data

    # decode header section to scan for Content-Length
    headers_bytes = data[:header_end].decode("iso-8859-1", errors="replace")
    content_length = get_content_length(headers_bytes)

    body_start = header_end + 4
    body = data[body_start:]

    # if there is a body (e.g. POST), read the remaining bytes
    if content_length is not None:
        while len(body) < content_length and len(data) < 10 * 1024 * 1024:
            chunk = sock.recv(BUFFER_SIZE)
            if not chunk:
                break
            data += chunk
            body = data[body_start:]

    return data


def get_content_length(headers_text: str) -> int | None:
    """assil halawi: scan header lines to find the Content-Length value."""
    for line in headers_text.split("\r\n")[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k.strip().lower() == "content-length":
            try:
                return int(v.strip())
            except ValueError:
                return None
    return None


def forward_http_request(req) -> bytes:
    """
    assil halawi: open a fresh TCP connection to the target server,
    send the rebuilt request, and read the full response.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # timeout prevents a dead or slow server from blocking the thread indefinitely
    server_sock.settimeout(10)
    server_sock.connect((req.host, req.port))

    try:
        # ali rida: build_forward_request converts to origin-form and removes hop-by-hop headers
        out_request = build_forward_request(req)
        server_sock.sendall(out_request)

        response = b""
        while True:
            chunk = server_sock.recv(BUFFER_SIZE)
            if not chunk:
                break
            response += chunk
            if len(response) > 20 * 1024 * 1024:
                # 20 MB hard limit to avoid unbounded memory use
                break
        return response
    finally:
        server_sock.close()


def send_forbidden(sock: socket.socket) -> None:
    """assil halawi: send a 403 response for blocked domains (blacklist or whitelist mode)."""
    body = b"403 Forbidden: This domain is blocked by the proxy.\n"
    resp = (
        b"HTTP/1.1 403 Forbidden\r\n"
        b"Content-Type: text/plain\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n"
        b"\r\n"
        + body
    )
    sock.sendall(resp)


def send_bad_gateway(sock: socket.socket) -> None:
    """assil halawi: send a 502 response when the proxy cannot reach the target server."""
    body = b"502 Bad Gateway: Proxy could not reach the target server.\n"
    resp = (
        b"HTTP/1.1 502 Bad Gateway\r\n"
        b"Content-Type: text/plain\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n"
        b"\r\n"
        + body
    )
    sock.sendall(resp)


if __name__ == "__main__":
    main()
