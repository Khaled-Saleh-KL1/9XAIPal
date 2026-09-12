# A caller-controlled deck ID can mutate another document's deck

**Severity:** High  
**Confidence:** Confirmed from endpoint, repository, and schema  
**Status:** Fixed on `fix/audit-findings`

## Resolution

Deck upserts now scope their conflict update to the document and reject a deck ID owned by another document before committing.

## What happens

A user who submits a deck UUID belonging to another document can update that
deck's label, top card, margin, and study mode. The subsequent member inserts
can also attach notes from the current document to that foreign deck.

## Why it happens

The PUT endpoint verifies ownership of paper_id and filters member note IDs to
that paper in backend/app/api/v1/endpoints/personal.py:320-358. It preserves
each caller-supplied deck.id without verifying that the deck belongs to the
same paper.

backend/app/database/repositories/personal.py:382-404 then performs INSERT ...
ON CONFLICT (id) DO UPDATE. The conflict branch updates the row selected by
the globally unique primary key but does not require
note_decks.document_id = the owned document. It also intentionally leaves the
foreign row's document_id unchanged, after which member rows are inserted
using that foreign deck ID.

## Root cause

Member ownership is validated, while container ownership is assumed. A global,
client-controlled primary key is used as an unconditional upsert target.

## Impact

Knowing another deck UUID permits cross-document and potentially cross-user
data corruption. The attacker can alter another user's layout and create
cross-document membership that the schema currently allows.

## Suggested fix

Reject every supplied deck ID that does not already belong to paper_id, and
generate new IDs server-side for new decks. Replace the unconditional upsert
with an UPDATE WHERE id = :id AND document_id = :document_id followed by an
insert for a server-approved new ID. Add a database invariant or trigger that
requires every member note to have the same document_id as its deck.
