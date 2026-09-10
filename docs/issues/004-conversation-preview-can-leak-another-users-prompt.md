# Conversation preview can leak another user's prompt

**Severity:** High  
**Confidence:** Confirmed from SQL data flow  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Conversation previews now correlate the latest turn by both user and document as well as conversation ID.

## What happens

The conversation list for one user can display the first prompt from another
user when both have rows with the same conversation UUID. Conversation UUIDs
are accepted from clients by the ask endpoints, so a collision can be
deliberately created if a victim UUID becomes known.

## Why it happens

In backend/app/database/repositories/conversations.py:188-218, the outer query
correctly filters ct by document_id and user_id. The correlated subquery at
lines 203-210 that selects first_user_message filters only by
ct2.conversation_id and role. It omits ct2.user_id and ct2.document_id.

PostgreSQL can therefore choose the earliest matching user turn from any
tenant or paper sharing that UUID.

## Root cause

Tenant scoping is present on the aggregate query but missing inside its
correlated preview subquery. conversation_id is treated as globally trusted
even though it is caller-controlled and has no global uniqueness constraint.

## Impact

A conversation title/preview can expose the text of another user's question.
The leak is limited to the selected first prompt, but prompts may contain
sensitive document or research details.

## Suggested fix

Correlate the subquery on user_id and document_id as well as conversation_id,
or compute the preview from the already filtered row set. Add a two-user
regression test where both users deliberately use the same conversation UUID.
