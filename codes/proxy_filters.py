# ==================================================
# file: proxy_filters.py
# primary contributor: ali rida
# contributions:
# - domain filtering logic (blacklist and whitelist)
# - subdomain matching
# - filter state management
# team support:
# - assil halawi (integration with proxy_server.py)
# - reina harake (admin ui integration)
# ==================================================

from __future__ import annotations


# ── blacklist ──────────────────────────────────────────────────────────────────
# domains that are always blocked when running in blacklist mode (the default)
BLACKLISTED_DOMAINS: set[str] = {
    "example.com",
    "tiktok.com",
}

# ── whitelist ──────────────────────────────────────────────────────────────────
# domains that are allowed through when running in whitelist mode.
# all other domains are blocked in that mode.
WHITELISTED_DOMAINS: set[str] = set()

# ── mode flag ──────────────────────────────────────────────────────────────────
# False → blacklist mode (block listed domains, allow everything else)
# True  → whitelist mode (allow listed domains only, block everything else)
WHITELIST_MODE: bool = False


# ── public helpers used by proxy_server.py and proxy_admin_server.py ──────────

def get_filters_snapshot() -> dict:
    """Return a copy of the current filter state for the admin ui."""
    return {
        "whitelist_mode": WHITELIST_MODE,
        "blacklist": sorted(BLACKLISTED_DOMAINS),
        "whitelist": sorted(WHITELISTED_DOMAINS),
    }


def set_whitelist_mode(enabled: bool) -> None:
    """Switch between blacklist mode (False) and whitelist mode (True)."""
    global WHITELIST_MODE
    WHITELIST_MODE = bool(enabled)


# ── blacklist management ───────────────────────────────────────────────────────

def add_to_blacklist(domain: str) -> None:
    # normalize: strip spaces, lowercase, remove trailing dots
    d = _normalize(domain)
    if d:
        # set.add is idempotent
        BLACKLISTED_DOMAINS.add(d)


def remove_from_blacklist(domain: str) -> None:
    d = _normalize(domain)
    if d:
        # discard avoids KeyError if the domain was not in the set
        BLACKLISTED_DOMAINS.discard(d)


# ── whitelist management ───────────────────────────────────────────────────────

def add_to_whitelist(domain: str) -> None:
    d = _normalize(domain)
    if d:
        WHITELISTED_DOMAINS.add(d)


def remove_from_whitelist(domain: str) -> None:
    d = _normalize(domain)
    if d:
        WHITELISTED_DOMAINS.discard(d)


# ── core filtering decision ────────────────────────────────────────────────────

def is_blocked(host: str) -> bool:
    """
    Return True if the request to 'host' should be blocked.

    Blacklist mode (WHITELIST_MODE=False, default):
        Block the host if it matches any rule in BLACKLISTED_DOMAINS.
        Allow everything else.

    Whitelist mode (WHITELIST_MODE=True):
        Allow the host only if it matches a rule in WHITELISTED_DOMAINS.
        Block everything else.
    """
    host = (host or "").lower().strip()
    if not host:
        # malformed request with no host — block it as a safety measure
        return True

    if WHITELIST_MODE:
        # ali rida: in whitelist mode, block unless the host is explicitly allowed
        allowed = any(_domain_matches(host, rule) for rule in WHITELISTED_DOMAINS)
        return not allowed
    else:
        # ali rida: in blacklist mode, block only if the host matches a blacklist rule
        return any(_domain_matches(host, rule) for rule in BLACKLISTED_DOMAINS)


# ── internal helpers ───────────────────────────────────────────────────────────

def _normalize(domain: str) -> str:
    """Strip whitespace, lowercase, remove trailing dots."""
    return (domain or "").strip().lower().strip(".")


def _domain_matches(host: str, rule: str) -> bool:
    """
    Return True if 'host' matches 'rule'.

    Two match cases:
    - Exact match:    host == rule          (e.g. google.com == google.com)
    - Subdomain match: host ends with .rule  (e.g. mail.google.com ends with .google.com)
    """
    host = host.lower().strip(".")
    rule = rule.lower().strip(".")
    return host == rule or host.endswith("." + rule)
