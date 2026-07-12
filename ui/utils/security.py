#!/usr/bin/env python3
"""
Anti-CSRF / DNS-rebinding guard for state-changing requests.

The API has no authentication yet (loopback bind is the main barrier) and
its POST endpoints execute real AWS actions or rewrite the config. A page
open in the local browser can fire cross-origin POSTs at localhost:8888:
the response stays unreadable, the side effects happen anyway. Until the
auth token lands, this middleware closes that path: write methods must
target a trusted Host (kills DNS rebinding, where an attacker domain
resolves to 127.0.0.1) and, when the browser sends an Origin, it must
name a trusted host too (kills classic CSRF).

GET stays open (read-only pages) and clients that send no Origin at all
(curl, local scripts, the tests) stay accepted — they are not browsers,
CSRF does not apply to them.
"""

import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# "testserver" is FastAPI TestClient's default Host. It is not a resolvable
# public name, so accepting it gives an attacker nothing: a DNS-rebinding
# domain always carries the attacker's own hostname.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def hostname(value: str) -> str:
    """Bare lowercase hostname out of a Host header or an Origin URL:
    'http://evil.com:8888' -> 'evil.com', '[::1]:8888' -> '::1'."""
    if "://" in value:
        value = urlsplit(value).netloc
    value = value.strip().lower()
    if value.startswith("["):  # bracketed IPv6, with or without port
        return value[1 : value.find("]")]
    if value.count(":") == 1:  # host:port (a bare IPv6 has 2+ colons)
        return value.rsplit(":", 1)[0]
    return value


def trusted_write_hosts() -> set:
    """Hosts allowed to receive write requests. Recomputed per request so a
    live process picks up env changes; the cost is a few getenv calls.

    WASTELESS_TRUSTED_HOSTS (comma-separated) covers deliberate network
    exposure behind an authenticated reverse proxy — the proxy's public
    hostname must be declared there or every browser write gets a 403.
    """
    hosts = set(_LOOPBACK_HOSTS)
    bind = hostname(os.getenv("WASTELESS_HOST", ""))
    # S104: ce n'est pas un bind — on EXCLUT l'adresse joker de la confiance
    if bind and bind != "0.0.0.0":  # noqa: S104
        hosts.add(bind)
    for extra in os.getenv("WASTELESS_TRUSTED_HOSTS", "").split(","):
        extra = hostname(extra)
        if extra:
            hosts.add(extra)
    return hosts


async def block_cross_origin_writes(request: Request, call_next):
    """FastAPI middleware — registered in ui/main.py."""
    if request.method in WRITE_METHODS:
        trusted = trusted_write_hosts()
        host_ok = hostname(request.headers.get("host", "")) in trusted
        origin = request.headers.get("origin", "")
        # 'null' (sandboxed iframe, some redirect chains) is a browser
        # context that cannot be trusted for writes.
        origin_ok = hostname(origin) in trusted if origin else True
        if not host_ok or not origin_ok:
            return JSONResponse(
                {
                    "error": "cross-origin write blocked: this endpoint only "
                    "accepts requests addressed to this machine (see "
                    "WASTELESS_TRUSTED_HOSTS to allow a reverse proxy host)"
                },
                status_code=403,
            )
    return await call_next(request)
