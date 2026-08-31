"""Shared SSRF guard: does a URL's host resolve only to public address space?

Used anywhere the server fetches a URL supplied (directly or indirectly) by a
client — a reader-chosen image (services/image_service.py), a web article to
import, or an image found inside one (services/article_extraction.py). A host
that resolves to private/loopback/link-local/reserved/multicast space is
refused outright; DNS failure is treated as unsafe too, since a host we can't
resolve is a host we shouldn't fetch from.

Two variants, one classifier: async for FastAPI request handlers, sync
(blocking) for Celery's worker, which runs everything synchronously — see
core/celery_app.py's module docstring.

safe_send_sync/safe_send_async close a second hole in the same guard: every
caller used to check only the URL it was GIVEN, then fetch it with httpx's
own follow_redirects=True. httpx never re-runs a caller's check against
where a redirect actually leads, so a host that legitimately resolves to a
public address can 302 the request anywhere it likes afterward — the cloud
metadata address, localhost, an internal service — and the check that just
ran is worthless. These walk the redirect chain themselves, hop by hop,
re-validating before following each one.
"""

import asyncio
import ipaddress
import socket
from typing import Union
from urllib.parse import urlparse

import httpx

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


def _is_unsafe_ip(ip: IPAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _any_unsafe(infos) -> bool:
    """True if any resolved address is unsafe, or couldn't even be parsed —
    an address this module can't classify is not one it should call safe."""
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if _is_unsafe_ip(ip):
            return True
    return False


async def resolves_to_private_address(url: str) -> bool:
    """Async variant, for FastAPI request handlers.

    Uses the loop's async getaddrinfo so DNS lookups never block the event
    loop.
    """
    host = urlparse(url).hostname
    if not host:
        return True
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
    except OSError:
        return True
    return _any_unsafe(infos)


def resolves_to_private_address_sync(url: str) -> bool:
    """Sync/blocking variant, for the Celery worker (sync throughout)."""
    host = urlparse(url).hostname
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return True
    return _any_unsafe(infos)


MAX_SAFE_REDIRECTS = 5


class UnsafeRedirectError(Exception):
    """A redirect chain led to (or couldn't be confirmed clear of) a
    private/internal address, or went on for too many hops to trust."""


def safe_send_sync(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    stream: bool = False,
    max_redirects: int = MAX_SAFE_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """Sync variant of the redirect-safe fetch — see the module docstring.

    Forces follow_redirects=False on every hop regardless of how `client`
    itself was constructed, so a caller's existing `httpx.Client(...)` needs
    no changes. On a stream=True call, the caller owns closing the returned
    response (this function only closes the intermediate redirect responses
    it consumes itself) — same contract as calling client.send() directly.
    """
    current = url
    for _ in range(max_redirects + 1):
        if resolves_to_private_address_sync(current):
            raise UnsafeRedirectError(f"refusing to fetch from private/internal address: {current}")
        request = client.build_request(method, current, **kwargs)
        response = client.send(request, follow_redirects=False, stream=stream)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        response.close()
        if not location:
            # Malformed redirect (3xx with no Location) — nothing more this
            # function can validate; hand it back as-is rather than guess.
            return response
        current = str(response.url.join(location))
    raise UnsafeRedirectError(f"too many redirects: {url}")


async def safe_send_async(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    stream: bool = False,
    max_redirects: int = MAX_SAFE_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """Async variant of safe_send_sync, for FastAPI request handlers."""
    current = url
    for _ in range(max_redirects + 1):
        if await resolves_to_private_address(current):
            raise UnsafeRedirectError(f"refusing to fetch from private/internal address: {current}")
        request = client.build_request(method, current, **kwargs)
        response = await client.send(request, follow_redirects=False, stream=stream)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        await response.aclose()
        if not location:
            return response
        current = str(response.url.join(location))
    raise UnsafeRedirectError(f"too many redirects: {url}")
