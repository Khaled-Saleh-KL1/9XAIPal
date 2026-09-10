# Image proxy limit is checked after buffering

**Severity:** High  
**Confidence:** Confirmed from HTTPX usage  
**Status:** Fixed on `fix/audit-findings`

## Resolution

The image proxy now uses an HTTP stream, rejects an excessive declared length early, and counts received chunks before buffering them.

## What happens

The image proxy's 12 MB limit does not prevent a remote server from making the
API download and buffer a much larger response.

## Why it happens

backend/app/services/image_service.py:120-132 calls safe_send_async without
stream=True. HTTPX therefore reads the response body before returning.
The code then accesses resp.content and only afterwards compares its length to
MAX_IMAGE_BYTES.

Content-Length is not sufficient by itself because it can be absent or false;
the actual stream must be counted.

## Root cause

The size check is placed after a non-streaming network request, so it validates
the cached result rather than constraining the resource use.

## Impact

An untrusted image URL from web/research results can consume arbitrary process
memory and bandwidth, potentially crashing an API worker.

## Suggested fix

Request with stream=True, reject an oversized declared Content-Length early,
then iterate response.aiter_bytes() into a bounded buffer or temporary file
while counting bytes. Close the response and delete partial output on every
exit path. Add a test server that streams beyond the cap without a
Content-Length header.
