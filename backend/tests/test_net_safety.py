"""safe_send_sync / safe_send_async: closing the redirect-based SSRF bypass.

Every URL-fetching call site used to check only the URL it was given, then
fetch with httpx's own follow_redirects=True — which never re-runs that
check against where a redirect actually leads. A publicly-reachable
attacker server could 302 the request to the cloud metadata address,
localhost, or an internal service, and the one check that ran before the
request started would never see it.

httpx.MockTransport intercepts the actual HTTP exchange, so these run fully
offline; resolves_to_private_address_sync's own DNS/IP classification runs
for real (127.0.0.1 and 169.254.169.254 are IP literals, no lookup needed).
"""

import httpx
import pytest

from app.core.net_safety import UnsafeRedirectError, safe_send_async, safe_send_sync


def _redirect_once(target: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "1.1.1.1" in str(request.url):
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=b"SHOULD NEVER BE REACHED")
    return handler


def test_safe_public_to_public_redirect_chain_succeeds():
    def handler(request: httpx.Request) -> httpx.Response:
        if "1.1.1.1" in str(request.url):
            return httpx.Response(302, headers={"Location": "http://8.8.8.8/final"})
        return httpx.Response(200, content=b"final content")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resp = safe_send_sync(client, "GET", "http://1.1.1.1/start")
    assert resp.status_code == 200
    assert resp.content == b"final content"


def test_redirect_to_loopback_is_refused_before_being_followed():
    client = httpx.Client(transport=httpx.MockTransport(_redirect_once("http://127.0.0.1/admin")))
    with pytest.raises(UnsafeRedirectError, match="127.0.0.1"):
        safe_send_sync(client, "GET", "http://1.1.1.1/start")


def test_redirect_to_cloud_metadata_address_is_refused():
    client = httpx.Client(transport=httpx.MockTransport(_redirect_once("http://169.254.169.254/latest/meta-data/")))
    with pytest.raises(UnsafeRedirectError, match="169.254.169.254"):
        safe_send_sync(client, "GET", "http://1.1.1.1/start")


def test_redirect_loop_raises_instead_of_hanging():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://1.1.1.1/loop"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeRedirectError, match="too many redirects"):
        safe_send_sync(client, "GET", "http://1.1.1.1/start", max_redirects=3)


def test_direct_response_with_no_redirect_passes_through_unaffected():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"direct hit")
    ))
    resp = safe_send_sync(client, "GET", "http://1.1.1.1/x")
    assert resp.status_code == 200
    assert resp.content == b"direct hit"


def test_original_url_being_private_is_still_caught():
    """The pre-existing single upfront check must keep working for the
    trivial case (no redirect involved at all)."""
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"should never be reached")
    ))
    with pytest.raises(UnsafeRedirectError):
        safe_send_sync(client, "GET", "http://127.0.0.1/direct")


async def test_async_redirect_to_loopback_is_refused():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_redirect_once("http://127.0.0.1/admin")))
    try:
        with pytest.raises(UnsafeRedirectError, match="127.0.0.1"):
            await safe_send_async(client, "GET", "http://1.1.1.1/start")
    finally:
        await client.aclose()


async def test_async_safe_redirect_chain_succeeds():
    def handler(request: httpx.Request) -> httpx.Response:
        if "1.1.1.1" in str(request.url):
            return httpx.Response(302, headers={"Location": "http://8.8.8.8/final"})
        return httpx.Response(200, content=b"final content")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        resp = await safe_send_async(client, "GET", "http://1.1.1.1/start")
        assert resp.status_code == 200
        assert resp.content == b"final content"
    finally:
        await client.aclose()
