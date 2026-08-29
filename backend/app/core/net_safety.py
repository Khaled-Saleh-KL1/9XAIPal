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
"""

import asyncio
import ipaddress
import socket
from typing import Union
from urllib.parse import urlparse

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
