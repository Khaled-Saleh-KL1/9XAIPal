# Book fetch errors are reported as end of content

**Severity:** Medium  
**Confidence:** Confirmed from exception path  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Network and server failures are retained as a retryable reader error; only a successful empty or boundary response marks the content as finished.

## What happens

A transient 401, timeout, connection loss, or backend 500 while loading the
next book chunk permanently changes the reader to its end-of-content state.
The normal reveal control disappears and there is no retry or error message.

## Why it happens

frontend/src/views/BookReadingView.tsx:481-523 uses atEnd for two unrelated
outcomes. A null/over-boundary response sets it at lines 488-496, and every
thrown fetch error sets the same flag at lines 519-520.

startReading also catches all errors at line 459 and continues with whatever
partial content was loaded, without retaining an error state.

## Root cause

Transport/server failure and successful exhaustion are represented by one
boolean. Exceptions are swallowed instead of being modeled as retryable load
failures.

## Impact

Brief network or session problems look like a finished/truncated book and
require navigating away or reloading to recover.

## Suggested fix

Use separate loadingError and atEnd states. Set atEnd only after a successful
response proves there is no next chunk or the chapter boundary was crossed.
Display a retry action that preserves lastSeq and current content. Keep
chapter completion changes limited to confirmed boundaries.
