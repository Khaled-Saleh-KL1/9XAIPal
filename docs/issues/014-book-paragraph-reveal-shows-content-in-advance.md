# Book paragraph reveal shows content in advance

**Severity:** Medium  
**Confidence:** Confirmed from state/render flow  
**Status:** Fixed on `fix/audit-findings`

## Resolution

The book reader keeps pending units separate from displayed units and reveals exactly one unit for each reader action.

## What happens

The book reader presents itself as revealing one paragraph or element at a
time, but every paragraph in each loaded chunk is visible immediately. Several
“next” actions then advance an internal counter without changing the page.
Fetching the next chunk reveals all of that chunk at once.

## Why it happens

frontend/src/views/BookReadingView.tsx:419-456 converts each loaded chunk into
all of its units and appends them to revealedUnits. The render loop displays
every item in revealedUnits.

revealNextUnit at lines 527-548 increments paragraphIndexInCurrent, but that
state is never used to slice or filter revealedUnits. fetchAndAppend at
lines 500-505 also appends every unit from the new chunk.

## Root cause

Two incompatible state models coexist: revealedUnits already contains
everything rendered, while paragraphIndexInCurrent assumes the current
chunk's units are still pending.

## Impact

Stepped reading exposes text ahead of the user's requested pace and makes
keyboard/click controls appear unresponsive for multi-paragraph chunks.

## Suggested fix

Keep loaded units separate from the visible cursor and render only through the
cursor, or append exactly one unit to revealedUnits per action. Fetch a new
chunk when the pending unit list is exhausted, then reveal only its first
unit. Add a component test using one chunk with three paragraphs.
