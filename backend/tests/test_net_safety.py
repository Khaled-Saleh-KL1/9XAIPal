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

from app.core.net_safety import (
    TooManyRedirectsError,
    UnsafeRedirectError,
    safe_send_async,
    safe_send_sync,
)


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


def test_streamed_redirect_chain_leaves_the_final_body_readable():
    """stream=True is the mode fetch_resource actually uses, and the one where
    closing the wrong response is unrecoverable: the intermediate 302 has to
    be closed, the final 200 must not be. An in-memory MockTransport response
    would hide the difference (its body is already buffered, so it reads back
    even from a closed response), so the final hop streams for real."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "1.1.1.1" in str(request.url):
            return httpx.Response(302, headers={"Location": "http://8.8.8.8/final"})
        return httpx.Response(200, content=iter([b"chunk-1", b"chunk-2"]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resp = safe_send_sync(client, "GET", "http://1.1.1.1/start", stream=True)
        try:
            assert b"".join(resp.iter_bytes()) == b"chunk-1chunk-2"
        finally:
            resp.close()


@pytest.mark.parametrize("location", ["http://xn--/", b"http://\xff.example/"])
def test_unparseable_location_is_refused_rather_than_crashing(location):
    """A broken Location header is the remote site's bug, not this server's.

    A malformed A-label raises idna.IDNAError and a raw non-ASCII byte raises
    UnicodeEncodeError. Neither is an httpx.HTTPError, so uncaught they escape
    every call site's except clauses — the reader is then told extraction
    failed and to restart the backend, over a header they can't act on.

    Both come out of client.send(), not url.join(): httpx parses the Location
    eagerly to build response.next_request even with follow_redirects=False.
    The bytes case is how a real server sends a non-ASCII byte; httpx refuses
    to build that header from a str at all.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafeRedirectError):
            safe_send_sync(client, "GET", "http://1.1.1.1/start")


def test_redirect_to_a_non_http_scheme_is_refused():
    """The http/https allowlist each caller applies to the URL it was given
    has to hold for every hop too. The target here is a public address, so it
    clears the private-address check — the scheme guard is the only thing that
    can refuse it, and httpx's own UnsupportedProtocol is luck, not a
    guarantee to rest a security check on."""
    handler = _redirect_once("gopher://8.8.8.8:6379/_FLUSHALL")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafeRedirectError, match="non-HTTP"):
            safe_send_sync(client, "GET", "http://1.1.1.1/start")


def test_a_long_chain_is_distinguishable_from_an_unsafe_one():
    """Both are refused, but a reader told "redirects somewhere that can't be
    imported" for a merely long chain is being blamed for someone else's
    redirect config — so the two have to be tellable apart, while still being
    caught by the existing `except UnsafeRedirectError` at every call site."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://8.8.8.8/next"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TooManyRedirectsError):
            safe_send_sync(client, "GET", "http://1.1.1.1/start", max_redirects=3)
        # Still caught by the broader except clause the call sites use.
        with pytest.raises(UnsafeRedirectError):
            safe_send_sync(client, "GET", "http://1.1.1.1/start", max_redirects=3)


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
