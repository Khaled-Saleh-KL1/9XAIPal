# Cross-origin frontend mode cannot stay authenticated

**Severity:** High  
**Confidence:** Confirmed from fetch defaults and URL construction  
**Status:** Fixed on `fix/audit-findings`

## Resolution

The shared API fetch wrapper always includes credentials, API-relative media paths are resolved against the configured backend origin, and SameSite mode is configurable for cross-site HTTPS deployments. The PDF viewer passes `withCredentials` to pdf.js, whose own fetch does not go through the wrapper and would otherwise reach the cross-origin `/raw` route with no cookie (found on the VPS follow-up, 2026-09-10).

## What happens

When VITE_API_BASE_URL points at an API on another origin, login/signup can set
a session but most of the application immediately receives 401 responses.
PDFs and extracted images are also requested from the frontend host instead
of the API host.

## Why it happens

frontend/src/api.ts supports an explicit API base URL, and auth calls at
lines 1723-1761 use credentials: "include". The roughly 50 protected fetches
elsewhere in the file omit credentials. Fetch defaults to same-origin, so it
does not send cookies to a cross-origin API.

getStaticPdfUrl at lines 1202-1205 returns /static/assets/... relative to the
SPA origin. Server-returned /static/images/... paths are also rendered as
relative URLs. Backend CORS allows credentials, but the clients and media URLs
do not consistently use that capability. If the two origins are also
cross-site, the backend's SameSite=Lax session cookie adds another blocker.

## Root cause

The API base URL and cookie policy were applied only to auth/export helpers
instead of through one shared request and media URL abstraction.

## Impact

The documented hosted-frontend deployment can authenticate but cannot use
papers, notes, studies, chats, or media reliably.

## Suggested fix

Create one apiFetch wrapper that always applies BASE and
credentials: "include", then migrate every API call to it. Convert server media
paths through an API-origin helper or return absolute/signed media URLs.
Choose and document whether supported deployments are cross-origin same-site
or fully cross-site; use an appropriate Secure/SameSite cookie policy for the
latter. Add an integration test with distinct SPA and API origins.
