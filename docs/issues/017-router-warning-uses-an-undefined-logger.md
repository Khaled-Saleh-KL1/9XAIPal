# Router warning uses an undefined logger

**Severity:** Low  
**Confidence:** Confirmed by static analysis  
**Status:** Fixed on `fix/audit-findings`

## Resolution

The context router now initializes its logger and records unexpected classifier failures before applying the documented fallback.

## What happens

When the classifier returns empty content or stops because of its output
length, the context router tries to emit a warning but raises NameError
instead. The surrounding broad exception catches that error, so routing falls
through to the generic default and the diagnostic is lost.

## Why it happens

backend/app/chat/router.py:166 calls logger.warning, but the module imports no
logger and initializes none. Lines 191-192 catch every exception and silently
pass, masking the programming error.

## Root cause

The diagnostic was added without adding get_logger/import initialization, and
an overly broad silent exception handler hides undefined names.

## Impact

Requests still receive a fallback route, but operators cannot distinguish an
empty/truncated classifier response from an unexpected router exception. The
returned reason also changes from the specific no-content reason to the
generic fallback.

## Suggested fix

Initialize logger with app.core.logging.get_logger(__name__) and log unexpected
classifier exceptions before returning the fallback. Add a focused test for an
empty classifier response; the current context-router test file is only a
placeholder.
