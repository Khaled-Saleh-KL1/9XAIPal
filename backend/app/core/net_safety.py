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


# httpx's own default is 20. A shorter budget is deliberate: every hop costs
# a DNS lookup plus a full request timeout, and on the --concurrency=1 worker
# that time is taken from every other document queued behind this one. 10
# still clears the longest legitimate chain seen in practice — a DOI resolver
# landing on a publisher, then its CDN, then a paywall check, then the PDF —
# which 5 did not.
MAX_SAFE_REDIRECTS = 10


class UnsafeRedirectError(Exception):
    """A redirect chain led to (or couldn't be confirmed clear of) a
    private/internal address, or named a target this module refuses to
    resolve at all."""


class TooManyRedirectsError(UnsafeRedirectError):
    """The chain never landed within `max_redirects` hops.

    A subclass so every existing `except UnsafeRedirectError` keeps catching
    it, while a caller that wants to tell a reader the difference can catch
    this first: a long-but-legitimate chain is not the same finding as a link
    pointing at the LAN, and reporting the second for the first blames the
    reader for someone else's redirect config (see
    article_extraction.fetch_resource).
    """


# A malformed Location header reaches this code as a URL-parsing failure, not
# an HTTP one: idna.IDNAError (a UnicodeError) for a broken A-label such as
# `http://xn--/`, UnicodeEncodeError for a raw non-ASCII byte, httpx.InvalidURL
# for the rest. None of them is an httpx.HTTPError, so uncaught they escape
# every call site's `except httpx.HTTPError` and surface to the reader as an
# internal server failure — for a header on someone else's page that they have
# no control over and cannot act on.
#
# ⚠ They surface from client.send(), NOT from url.join(): httpx parses the
# Location eagerly while building `response.next_request`, even when the send
# passes follow_redirects=False. Guarding only the join — the obvious-looking
# place, and where this was first written — catches none of them. Verified
# against httpx 0.28.1: url.join('http://xn--/') returns a URL happily and the
# IDNAError comes out of the send that produced the redirect.
_URL_PARSE_ERRORS = (UnicodeError, httpx.InvalidURL)


def _next_hop(response: httpx.Response, location: str) -> str:
    """Resolve a Location header against the URL that returned it.

    The http/https allowlist each caller applies to the URL it was GIVEN has
    to hold for every hop too, or a redirect walks straight out of it. httpx
    would refuse a `gopher://` hop by itself today (UnsupportedProtocol,
    raised before any I/O), but that is luck rather than a guarantee this
    module should rest a security check on.
    """
    try:
        target = str(response.url.join(location))
    except _URL_PARSE_ERRORS as e:
        raise UnsafeRedirectError(f"unusable redirect target: {location!r}") from e
    if not target.lower().startswith(("http://", "https://")):
        raise UnsafeRedirectError(f"refusing to follow a non-HTTP redirect: {target}")
    return target


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
        try:
            request = client.build_request(method, current, **kwargs)
            response = client.send(request, follow_redirects=False, stream=stream)
        except _URL_PARSE_ERRORS as e:
            raise UnsafeRedirectError(f"unusable redirect target from {current}: {e}") from e
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            # A 3xx carrying no Location. httpx's is_redirect is status-code
            # only (300/304/305 all land here), so this is reachable rather
            # than theoretical. There is nothing to follow and nothing to
            # validate, so hand it back UNREAD — closing it first would give
            # a stream=True caller a response whose body can never be read —
            # and let the caller's own raise_for_status() or is_success check
            # decide what a bare 3xx means to it.
            return response
        response.close()
        current = _next_hop(response, location)
    raise TooManyRedirectsError(f"too many redirects: {url}")


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
        try:
            request = client.build_request(method, current, **kwargs)
            response = await client.send(request, follow_redirects=False, stream=stream)
        except _URL_PARSE_ERRORS as e:
            raise UnsafeRedirectError(f"unusable redirect target from {current}: {e}") from e
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            # See the sync variant: returned unread, deliberately not closed.
            return response
        await response.aclose()
        current = _next_hop(response, location)
    raise TooManyRedirectsError(f"too many redirects: {url}")
