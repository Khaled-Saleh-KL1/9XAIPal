# AI reading order toggle is a no-op

**Severity:** Medium  
**Confidence:** Explicitly confirmed by code TODO  
**Status:** Fixed on `fix/audit-findings`

## Resolution

When enabled, the reader loads exact sequence IDs from the reconstructed order and restarts from the first unit in that order.

## What happens

After reconstructing reading order, enabling “Use AI-corrected reading order”
clears the visible content but subsequent reading still follows physical
sequence order.

## Why it happens

frontend/src/views/BookReadingView.tsx:1083-1102 stores useLogicalOrder and
resets revealedUnits. The adjacent TODO at lines 1095-1097 states that the
reveal loop still walks document order and nothing consumes readingOrder.

startReading and fetchAndAppend call range/after endpoints by sequence number;
neither maps its cursor through readingOrder.

## Root cause

The reconstruction result was wired into controls and state but never into
the data-loading/reveal algorithm.

## Impact

Users are told corrected two-column/complex-layout order is active when it is
not. Toggling it also discards the current display, making the failure more
disruptive.

## Suggested fix

When logical mode is enabled, maintain an index into readingOrder and fetch
the specified sequence IDs, preferably with a bulk endpoint preserving the
requested order. Restore progress in logical-index terms or map saved physical
IDs safely. Hide/disable the toggle until this path exists, and test a
non-monotonic order such as [1, 3, 2].
