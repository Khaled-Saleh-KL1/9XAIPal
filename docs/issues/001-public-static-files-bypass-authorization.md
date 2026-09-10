# Static document and research files bypass authorization

**Severity:** High  
**Confidence:** Confirmed by code inspection  
**Status:** Fixed on fix/audit-findings

## What happens

An unauthenticated request can read files below /static/images,
/static/extracted, and /static/assets when it knows the path. That includes
original uploaded PDFs, extracted figures, cached proxy images, and persisted
research images.

For example, an owned PDF is available through the authenticated
/api/v1/papers/{paper_id}/raw endpoint, but the same bytes are also stored as
assets/{paper_id}.pdf and exposed at /static/assets/{paper_id}.pdf without a
session or ownership check.

## Why it happens

backend/app/main.py:42-51 mounts the storage directories with Starlette
StaticFiles. StaticFiles serves a matching path directly and cannot run the
document-level ownership checks used by the API endpoints.

The UUID-based paths make accidental discovery less likely, but a UUID is an
identifier, not authorization. IDs and image URLs are returned to browsers,
appear in histories and logs, and can be shared or leaked.

## Root cause

Private user data and public web assets share the same unauthenticated static
serving mechanism. Authorization is applied to metadata/API routes but not to
the underlying file route.

## Impact

Anyone with a leaked path can download another user's PDF or image without
logging in. Research image URLs also remain usable outside the conversation
that owns them.

## Suggested fix

Serve private files through authenticated endpoints that resolve the
document/conversation and verify its owner before returning FileResponse.
Keep only intentionally public frontend assets on StaticFiles. If image tags
need direct URLs, use short-lived signed URLs or cookie-authenticated media
routes and add tests that anonymous and cross-tenant requests return 401/404.

## Resolution

Private StaticFiles mounts were removed. Extracted assets now use an
authenticated paper route that verifies both document ownership and the stored
asset record before opening a path. Research images use an authenticated
conversation route. The PDF viewer reuses the existing authenticated raw-file
route. Focused tests cover URL generation, path traversal rejection, route
registration, and agent-generated figure links.
