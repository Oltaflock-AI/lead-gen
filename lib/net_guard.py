"""SSRF guard for server-side fetches of lead-supplied URLs (F07/F09).

A lead's `website` field comes from Google Places or a web search and is
attacker-influenceable, so any server-side fetch of it must refuse private,
loopback, link-local, and reserved addresses — and re-validate on every
redirect hop (an allowed public URL can 302 into an internal one).

Note: this resolves and checks the hostname's IPs before connecting, which
closes the common SSRF cases. It does not fully defeat DNS-rebinding (a TOCTOU
between resolve and connect); pinning the resolved IP into the socket would be
required for that and is out of scope for this app's threat level.
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests


class BlockedURLError(Exception):
    """Raised when a URL resolves to a disallowed (private/reserved) address."""


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


def _validate(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise BlockedURLError(f"blocked scheme: {p.scheme!r}")
    host = p.hostname
    if not host or not _host_is_public(host):
        raise BlockedURLError(f"blocked host: {host!r}")


def safe_get(url: str, *, max_redirects: int = 5, **kwargs) -> requests.Response:
    """Like requests.get, but validates the target and every redirect hop
    against private/reserved ranges before connecting. Forwards **kwargs
    (headers, timeout, stream, ...). Raises BlockedURLError if disallowed."""
    kwargs["allow_redirects"] = False
    current = url
    for _ in range(max_redirects + 1):
        _validate(current)
        r = requests.get(current, **kwargs)
        if r.is_redirect or r.is_permanent_redirect:
            loc = r.headers.get("Location")
            if not loc:
                return r
            current = urljoin(current, loc)
            continue
        return r
    raise BlockedURLError("too many redirects")
