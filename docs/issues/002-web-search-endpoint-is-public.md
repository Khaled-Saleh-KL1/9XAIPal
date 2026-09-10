# Web search can spend provider quota without authentication

**Severity:** High  
**Confidence:** Confirmed by code inspection  
**Status:** Fixed on `fix/audit-findings`

## Resolution

The endpoint now requires an admitted session, constrains query and result sizes, and applies a Redis-backed per-user rate limit.

## What happens

GET /api/v1/search/web?q=... invokes the configured web-search cascade for an
anonymous caller. Depending on configuration, each request can consume paid
Tavily, Linkup, Exa, or SerpApi quota.

## Why it happens

backend/app/api/v1/endpoints/search.py:20-28 declares the endpoint without
Depends(get_current_user) and without the stricter limiter used for expensive
LLM operations. backend/app/api/v1/router.py includes this router directly.

The generic middleware allows 300 API requests per minute by default and is
kept separately in each worker. It reduces a simple flood but still permits
large anonymous quota consumption and scales its effective allowance with
worker count.

## Root cause

Authentication was omitted at the external-service boundary. The endpoint
looks like a read-only search route, but it performs billable network work.

## Impact

An external caller can exhaust provider quotas, create unexpected cost, trip
provider circuit breakers, and degrade searches for authenticated users.

## Suggested fix

Require get_current_user and add a Redis-backed per-user limit sized for search
cost. Constrain q and limit with Pydantic/FastAPI bounds. Add an HTTP test that
an anonymous call receives 401 and does not invoke the provider.
