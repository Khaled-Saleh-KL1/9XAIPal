# PDF upload limit is checked after buffering

**Severity:** High  
**Confidence:** Confirmed from request path  
**Status:** Fixed on `fix/audit-findings`

## Resolution

PDF uploads stream in fixed-size chunks, enforce the byte limit while writing, validate the header from the bounded prefix, and remove partial files on failure.

## What happens

The configured maximum upload size does not limit how much memory one request
can allocate. The backend reads the entire multipart file into a bytes object
before deciding whether it is too large.

## Why it happens

backend/app/api/v1/endpoints/documents.py:69-74 calls await file.read(), then
checks len(content) against max_bytes. UploadFile may spool incoming data, but
read() without a size still creates a full in-memory bytes value. Accepted
content is then retained while being written to two locations.

A reverse proxy may impose a separate limit in some deployments, but direct,
development, LAN, or differently configured deployments reach this code
without that protection. Concurrent requests multiply peak memory.

## Root cause

Application-level validation is performed after full materialization instead
of while streaming.

## Impact

An authenticated user or accidental oversized upload can exhaust worker
memory and terminate the API before it can return 413.

## Suggested fix

Read and write fixed-size chunks while maintaining a byte counter. Abort,
close, and delete the partial file as soon as the counter exceeds max_bytes.
Avoid keeping the full PDF in memory and copy/stream the accepted file to its
second destination. Keep a proxy limit as an additional outer bound.
