# Conversation IDs are not bound to the paper

**Severity:** Medium  
**Confidence:** Confirmed from request-to-query flow  
**Status:** Fixed on `fix/audit-findings`

## Resolution

All conversation and thread reads are document-scoped, and a conversation ID cannot be reused across users or papers.

## What happens

A user can open paper B while supplying a conversation_id or
thread_root_turn_id from paper A. The API then returns A's turns under B's URL,
or injects A's recent conversation into a new answer about B.

## Why it happens

backend/app/api/v1/endpoints/ask.py:62-90 verifies paper ownership but does not
verify that payload conversation/thread IDs belong to that paper.
get_paper_chat repeats the same split at lines 245-303: it verifies paper_id,
then loads a conversation or subtree using only user_id and the supplied ID.

backend/app/database/repositories/conversations.py:128-143 and get_main_chat at
lines 229 onward filter by user_id and conversation_id, not document_id.
backend/app/chat/orchestrator.py uses get_conversation_history to build model
context, so this is not limited to display.

## Root cause

Authorization checks establish that both the user and paper are valid, but no
relational check binds the secondary conversation identifiers to that paper.

## Impact

Chat history from one owned document can appear in another and influence its
answers, producing confusing or incorrect responses and saving mixed-document
turns under one conversation UUID.

## Suggested fix

Use the existing document-aware list_turns_by_conversation query everywhere a
paper route loads history. Validate thread roots and parents against user_id,
document_id, and conversation_id before generation or persistence. Return 404
for mismatches and cover cross-paper IDs in endpoint tests.
