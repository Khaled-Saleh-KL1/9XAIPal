# One line for Semantic Scholar: the citation queue

> **What this is:** what changed the day the `SEMANTIC_SCHOLAR_API_KEY`
> landed (2026-09-11) — two things the key exposed that the unauthenticated
> 429 wall had hidden, and the queue readers now stand in.
>
> **How to read it:** §1 the two findings → §2 the line (`core/pacer.py`) →
> §3 the title guess → §4 the stream and the chip → §5 what was verified.
>
> **Companions:** [clickable-citations.md](clickable-citations.md) (the feature
> this sits under) · [api.md](../03-reference/api.md) (`/resolve/stream`) ·
> [configuration.md](../03-reference/configuration.md#bibliography-citations).
>
> **Status:** shipped · **Reflects code as of:** 2026-09-11

---

## 1. What the key exposed

Clickable citations shipped 2026-09-09 without a key, and every live call
was 429, so the code past the HTTP call had never run against a real
answer. With the key, two things surfaced within minutes:

1. **`/paper/search/match` is a title matcher.** Given the whole entry —
   `Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer
   normalization. arXiv preprint arXiv:1607.06450, 2016.` — it answers 404
   "Title match not found". Given `Layer normalization` it matches at once.
   The relevance `/paper/search` endpoint does no better on the full string
   (`total: 0`). The first live resolve therefore came back `no_match` —
   and `no_match` is cached **permanently** as a completed lookup. Every
   citation in the corpus would have been dead on first click.
2. **"1 request per second" is enforced loosely, and per key.** Measured
   live with 1.05 s, 1.5 s and 2 s between requests, a third to a half came
   back 429 with no discernible pattern. And the allowance is for the key,
   i.e. the whole box — but this API runs `--workers 2`, and any number of
   readers can open citations at the same instant.

Khaled's ask for the second: hold everyone, serve one per second in order,
and tell the ones waiting that the link will open shortly.

## 2. The line — `core/pacer.py`

One Lua script in Redis, so taking a turn is atomic across both workers:
read the timestamp the *next* request may fire at, push it forward by one
interval for whoever comes after, hand the caller its slot. Arrival order
is turn order; no polling, no lock held across the wait. Redis's `TIME` is
the clock, so two workers with drifting clocks agree on the line, and the
caller sleeps the *delta* the script computed, never an absolute time.

```text
   reader A ─┐                     ┌─ slot 0  (fires now)
   reader B ─┼─ take_turn (Lua) ──▶├─ slot 1  (+1.05 s)   ← told "#1, next up"
   reader C ─┘                     └─ slot 2  (+2.10 s)   ← told "#2"
```

A slot is reserved the moment it is taken, whether or not the caller lives
to use it (a closed tab mid-wait). That costs one idle second of provider
time; the alternative — releasing slots — needs a lock held across the
sleep and reintroduces the race this exists to remove. Redis unreachable
degrades to "no pacing" with a warning rather than to a hard failure: a
missing turnstile makes a 429 likelier, which the retries below absorb,
while an exception would fail every lookup for an infrastructure blip.

**429 is retried, in the line.** Because a correctly spaced request still
gets 429 a third of the time, a 429 is not a failure: the call re-queues
(a fresh slot — fair to whoever arrived meanwhile) and backs off one extra
interval per attempt, up to `SEMANTIC_SCHOLAR_MAX_ATTEMPTS` (4). Only when
those are spent does it become `unavailable`, which is never cached, so the
chip's Retry gets a fresh run. The circuit breaker only sees the final
failure, not each 429, or four readers in a burst would have tripped it.

## 3. The title guess — `services/references.py::title_candidates`

The raw entry is split into sentence-ish segments and the title is the
first segment after the author list that is not venue-shaped. Two shapes
cover the corpus and the standard styles: `Authors. Title. Venue, year.`
(arXiv/ACL/APA) and `Authors, "Title," in Venue` (IEEE — a quoted title is
taken alone, unambiguous). Periods that are *not* sentence ends are
protected before the split — author initials (`Quoc V. Le`), `Proc.`, `pp.`
— but `et al.` deliberately is not, because it ends the author list and the
title starts right after it. Segments with volume/page shapes
(`9(8):1735–1780`, `pages 770–778`) or venue lead-ins (`In `, `arXiv`,
`CoRR`) are skipped. Up to two guesses, one call each, first hit wins; an
entry that is just a title is its own guess. 14 real entries from the
corpus and the IEEE/APA variants all yield the exact title first.

## 4. The stream and the chip

`GET /references/{n}/resolve/stream` is the JSON route as SSE. The lookup
runs in its own session and task; the pacer's `on_queued` callback puts a
`queued` event (position, estimated wait) on a queue the generator drains,
so the reader is told *before* the sleep — and told again if a 429
re-queues them. `resolved` carries the entry when the turn comes. Ownership
and the 404 are checked before the stream opens, so those stay HTTP errors.
The JSON route remains for anything that just wants to wait.

The chip (`BibCitationRef`) shows "Looking it up…" until a `queued` event,
then "In the queue — #3, the link will open shortly…" in accent (not faint:
a several-second wait must read as *in progress, on purpose*, not as a
stuck spinner), then the resolved title with a **PDF ↗** link when Semantic
Scholar found an open-access copy, and "Add to library". The link is a link
the reader clicks, not a tab opened for them: a `window.open` fired seconds
after the click, from a stream callback, is exactly what popup blockers
exist to stop.

## 5. Verified

- **Live, with the key**, on a throwaway API with the *Attention* paper's
  40 parsed references: one resolve → `Layer Normalization` (2016, arXiv
  1607.06450). Then **eight readers at the same instant** (refs 2–9): all
  eight told their place (`#1 ~1.0s` … `#7 ~7.3s`), served one per second
  in arrival order, four of them hit a 429 and were re-queued (told their
  new place), all eight resolved to the right paper within 14 s.
- The 429 measurement in §1, three spacings, six requests each, plus the
  light `/paper/{id}` endpoint showing the same pattern.
- `tests/test_pacer.py` on real Redis: ten simultaneous callers get ten
  distinct slots and positions; `wait_turn` spaces real calls ≥ interval in
  arrival order; `on_queued` fires only when there is a wait; Redis outage
  → no pacing; the client never burns a slot without a key and relays the
  place. `tests/test_reference_titles.py`: every corpus shape above.
- The chip in a real DOM (happy-dom + react-dom, fake stream): "Looking it
  up…" → "In the queue — #3, the link will open shortly…" → title, PDF
  link, Add to library. `tsc` and `vite build` clean.
- Live DB: all 40 `paper_references` rows `pending`, 13 documents
  `self_resolve_status='pending'` — nothing wrongly cached to repair.
