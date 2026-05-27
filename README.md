## Simple Python Caching Proxy (Sockets + Threads)

A small, beginner-friendly **HTTP proxy** with:
- **HTTP forwarding**
- **HTTPS forwarding (BONUS)** via **CONNECT tunneling** (no decryption / no inspection)
- **Caching** for `GET` responses (in-memory, TTL-based)
- **Domain blocking** (blacklist + whitelist)
- **Logging** to `proxy.log`
- **Admin interface (BONUS)** for stats, logs, cache, filters, and active connections

## Project structure (what each file does)

- **`proxy_server.py`**: Main entrypoint. Listens for clients, parses requests, enforces blacklist, serves cache hits, forwards HTTP, and handles `CONNECT` tunnels. Also starts the admin UI.
- **`proxy_http.py`**: HTTP parsing + request rebuilding. Extracts target `host/port/path`, builds the forwarded request, parses response headers, and computes cache TTL from `Cache-Control`/`Expires`.
- **`proxy_https.py`**: HTTPS `CONNECT` tunneling. Creates a TCP tunnel and relays bytes both directions (no MITM).
- **`proxy_cache.py`**: In-memory cache with expiration. Stores raw response bytes keyed by full URL.
- **`proxy_filters.py`**: Domain blacklist and whitelist rules + helpers used by the proxy and admin UI (exact match or subdomain match).
- **`proxy_state.py`**: Shared live state across threads (stats + active/recent connections) used by the admin UI.
- **`proxy_admin_server.py`**: Small threaded web UI (`/`, `/active`, `/cache`, `/filters`, `/logs`) to observe and control the proxy during testing.
- **`proxy_logging.py`**: File logger configuration (writes `proxy.log`).


## Requirements

- Python 3.10+ recommended (works on Windows/macOS/Linux)
- `curl` recommended for testing (browser testing works too)


## Run the project

Open a terminal in this folder and run:

```bash
python proxy_server.py --host 127.0.0.1 --port 8888
```

## End-to-end testing (commands for each feature)

Keep the proxy running, then use the commands below.

### 1) HTTP forwarding

```bash
curl.exe -x http://127.0.0.1:8888 http://httpbin.org/get 
```


### 2) HTTPS forwarding (CONNECT tunneling)

```bash
curl.exe -x http://127.0.0.1:8888 https://google.com/ 
```

### 3) Caching (GET cache hit/miss)

```bash
curl.exe -x http://127.0.0.1:8888 http://httpforever.com/ 
```

### 4) Blocking 
### a) blacklist

```bash
curl.exe -x http://127.0.0.1:8888 http://example.com/
```

Expected result:
- HTTP status is **403 Forbidden**
- `proxy.log` contains a `BLOCKED | ... host=example.com ...` entry



### b) whitelist 

Open the filters page and add a domain to the whitelist:
- Open `http://127.0.0.1:8890/filters`
- Add `httpforever.com` to the whitelist
- Click "Switch to Whitelist Mode"

Then test:

```bash
curl.exe -i -x http://127.0.0.1:8888 http://httpforever.com/ 
```

Expected: 200 OK — whitelisted domain passes through.

```bash
curl.exe -i -x http://127.0.0.1:8888 http://httpbin.org/get
```
Expected: 403 Forbidden — not in the whitelist, blocked regardless of blacklist.

Switch back to blacklist mode from the filters page when done.


### 5) Admin interface (dashboard, logs, cache, filters, active connections)

Open these pages in a browser:
- Dashboard: `http://127.0.0.1:8890/`
- Active + recent connections: `http://127.0.0.1:8890/active`
- Cache view/clear: `http://127.0.0.1:8890/cache`
- Filters (blacklist add/remove): `http://127.0.0.1:8890/filters`
- Logs (tail): `http://127.0.0.1:8890/logs`


### 6) Active connections demo (so it shows up on `/active`)

Fast HTTP requests may finish too quickly to see as “active”, so use a request that stays open briefly.
One simple way is to start a long HTTPS fetch and quickly refresh `/active`:

```bash
python -c "import socket,time; s=socket.create_connection(('127.0.0.1',8888)); s.sendall(b'CONNECT google.com:443 HTTP/1.1\r\nHost: google.com:443\r\n\r\n'); print(s.recv(4096)); time.sleep(15); 
s.close()" 
```


## Blacklist (what is blocked and why)

Current blacklist rules live in `proxy_filters.py` as `BLACKLISTED_DOMAINS`.

- **Blocked domains (default)**: `example.com`
- **Why this domain**: it is a stable, public test domain that’s safe to use in demos; blocking it makes it easy to show a clear **403** path without relying on random websites.




## Team contributions

- **Assil Halawi** — core proxy server, socket programming, threading, HTTP forwarding, server infrastructure
- **Reina Harake** — caching, logging, live state tracking, admin interface
- **Ali Rida** — HTTP request parsing, domain filtering (blacklist and whitelist), HTTPS tunneling
- **All members** — admin interface features, integration testing, documentation