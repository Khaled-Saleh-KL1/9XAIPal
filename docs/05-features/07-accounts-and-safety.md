# Area 7 — Accounts, safety, capacity (features 86–94)

> Part of the [feature catalogue](README.md). Companion architecture doc:
> [auth.md](../02-architecture/auth.md); the 2026-09-10 audit reports in
> [docs/issues](../issues/000-code-audit-index.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

Four invariants hold everywhere: every route except `/health` and `/auth/*` requires a session,
enforced by the `get_current_user` dependency, not by convention; a resource you don't own
**404s, never 403** (a 403 confirms it exists); the session is a random opaque token, never a JWT;
password comparison and "does this email exist" never differ in observable timing.

---

## 86. Open signup, login, logout, `/me`

**What it does.** Anyone who can reach the API can create an account (no invite code); login sets
an httponly session cookie; `/me` returns the user and whether they are admitted.

**Where.** [`endpoints/auth.py`](../../backend/app/api/v1/endpoints/auth.py),
[`core/auth.py`](../../backend/app/core/auth.py), [`AuthView.tsx`](../../frontend/src/views/AuthView.tsx),
[`contexts/AuthContext.tsx`](../../frontend/src/contexts/AuthContext.tsx),
[`components/UserMenu.tsx`](../../frontend/src/components/UserMenu.tsx).

**How it works.** The session is `secrets.token_urlsafe(32)` (256 bits) stored in Redis as
`session:<token> → {user_id}` with a **30-day sliding TTL** refreshed on every authenticated
request, so an active reader is never logged out mid-session; only inactivity expires it. The
cookie is `httponly; SameSite=<SESSION_COOKIE_SAMESITE, default lax>; Secure=not DEBUG; Path=/`.
Login always mints a fresh token (never reuses one from an incoming cookie — session fixation).
A wrong password and an unknown email both run a real Argon2 verify (against a precomputed dummy
hash when the user does not exist) and both answer `401 Invalid email or password`. Signup's
"email exists" check answers `409` with a message that does not say which detail was wrong, and
a race between two signups for the same email is caught by the `LOWER(email)` unique index and
mapped to the same 409 rather than a 500 ([docs/issues/018](../issues/018-concurrent-signups-return-500.md)).
Logout deletes the Redis key and frees the capacity slot immediately. The client side is the
name badge in every view's header (`UserMenuInline`): click it, one red **Sign out** item. ⚠ The
menu is portalled to `<body>` at a fixed position rather than dropped inside the badge — the
library's header row is `overflow-x: auto` so it can swipe on a phone, and an overflow-x that is
not `visible` clips overflow-y too, so the old inline dropdown was drawn and cut off at the
header's edge: "I can't sign out" was true on the library and false on the Desk.

**Why opaque-and-server-side, not a signed cookie.** A signed cookie needs no storage but cannot
be revoked short of rotating the signing secret, which logs everyone out at once. Redis is already
a hard dependency (Celery's broker), so an opaque token costs zero new infrastructure and buys real
per-session revocation. `SameSite=Lax` needs no CSRF token because every mutating route is a JSON
POST with a non-simple content type — a cross-origin request needs a CORS preflight, and
`CORSMiddleware` never allows a wildcard alongside credentials; `none` exists for a hosted SPA on a
different *site* ([docs/issues/013](../issues/013-cross-origin-frontend-mode-cannot-stay-authenticated.md)).
`get_current_user_optional` (used by `/me` only) never raises `NotAdmitted`, so a queued user's tab
can keep polling to learn when it is let in.

---

## 87. Per-user data isolation

**What it does.** Every resource belongs to exactly one user; nothing lists or fetches without a
caller-supplied owner.

**Where.** every repository function on `documents`, `studies`, `sticky_notes`,
`conversation_turns` takes `user_id`; child tables trust the parent check at the endpoint
boundary; `repositories/conversations.py` (`conversation_is_available_for_document`,
`get_turn_for_document`), `repositories/personal.py::replace_decks`.

**How it works.** Top-level owner tables carry `user_id` and filter on it in every `WHERE`. Child
tables (`chunks`, `paper_notes`, `chunk_embeddings`, …) have no `user_id`: ownership is established
once by loading the parent scoped to the caller (`get_document(db, paper_id, user_id)` → 404 if
missing *or* someone else's), and every child lookup in that request trusts the verified id. The
2026-09-10 audit closed the gaps that pattern had missed: a conversation id is bound to `(user,
document)` so history cannot bleed between papers or tenants, and a client-supplied `parent_turn_id`
/ `thread_root_turn_id` is loaded scoped to both before use
([005](../issues/005-conversation-ids-are-not-bound-to-the-paper.md)); the conversation-list
preview subquery is tenant-filtered ([004](../issues/004-conversation-preview-can-leak-another-users-prompt.md));
a deck id may only update a deck of *this* document ([003](../issues/003-deck-upsert-can-mutate-another-document.md)).
Global-scan endpoints were the actual historical risk: an unscoped `search/vector` and two
`pgvector.py` helpers that scanned every user's chunks when called with no document filter — they
now return `[]` instead.

---

## 88. Auth rate limiting

**What it does.** Ten login/signup attempts per IP per minute; past that, `429` with `Retry-After`.

**Where.** `api/deps.py::enforce_auth_rate_limit` (Redis key `authrl:<ip>`, `INCR` + `EXPIRE`),
plus the generic per-process `RateLimitMiddleware` in `core/security.py`.

**Why two.** The app-wide middleware is deliberately generic and, per its own docstring, is not
even correctly shared across the API's `--workers 2` — nowhere near tight enough to blunt
credential stuffing. The auth limiter is a separate, stricter, Redis-backed one that works
regardless of worker count. ⚠ This is the one rate limit that stays: it throttles attackers, not
readers. (Feature limits on logged-in users are a different matter — see feature 71.)

---

## 89. Concurrent-user cap and the waiting room

**What it does.** At most `MAX_ACTIVE_USERS` (30) people use the site at once; the 31st sees a
waiting-room screen with their position and is let in automatically when a slot frees.

**Where.** [`core/capacity.py`](../../backend/app/core/capacity.py) (`touch_and_check_admission`,
`release`), `api/errors.py::NotAdmitted` (→ **423 Locked**, `{"code":"NOT_ADMITTED",
"queue_position": N}`), [`WaitingRoomView.tsx`](../../frontend/src/views/WaitingRoomView.tsx),
`ACTIVE_WINDOW_SECONDS` (300).

**How it works.** "Active" is a **5-minute sliding window**, not "has a session" — sessions last
30 days, and using that as the signal would fill the cap permanently after the 30th person ever
logged in. `active_users` is a Redis sorted set scored by last-seen; every authenticated request
refreshes it. Admission is **sticky**: a newcomer can never bump someone already in; a slot frees
by idling past the window or by logging out. Everything — expiry sweep, the sticky check, the
count, the FIFO queue with per-user heartbeats, promotion of the head — runs in **one Redis Lua
script**, so two workers cannot both admit a 31st user, and a queue head that stopped polling is
dropped rather than blocking the line ([006](../issues/006-capacity-admission-is-neither-atomic-nor-fifo.md)).
Enforced, not decorated: `get_current_user` raises for every protected route, so a queued user's
requests are actually rejected. The waiting-room view polls `/me` every few seconds and the app
swaps in with no reload once `admitted` flips.

**Why.** This is what actually protects a single box with no autoscaling — signup is open, and
the cap, not an invite code, bounds concurrent load.

---

## 90. Concurrent-signup safety

**What it does.** Two people signing up with the same email at the same instant get one `201` and
one `409` — never a `500`.

**Where.** `auth.py::signup` (`IntegrityError` with `sqlstate 23505` → 409), the functional
`LOWER(email)` unique index.

**Why.** The pre-check ("email exists?") is not atomic; the index is. Verified on the VPS: six
concurrent signups → one 201, five 409.

---

## 91. Authenticated file serving

**What it does.** Figures, PDFs and research images are served only to their owner.

**Where.** `GET /papers/{id}/assets/{file_path}` (`chunks.py::get_paper_asset`, `_local_asset_file`),
`GET /papers/{id}/raw`, `GET /media/research/{conversation_id}/{filename}` (`media.py`),
`repositories/assets.py::resolve_asset_url`, `file_path_belongs_to_document`.

**How it works.** Each route loads the owning document (or a turn of the conversation) scoped to
the caller first; `/assets/` additionally requires the path to name a `chunk_assets` row of that
document, then resolves it below `images_dir()` and rejects `..`, absolute paths and anything that
escapes the root; `filename` on the research route must be a bare name. Nothing under the storage
root is mounted publicly; raw MinerU output (`extracted/`) is never served.

**Why.** Before 2026-09-10 the whole storage root was a public `StaticFiles` mount: anyone with a
leaked path read another user's PDF with no login ([001](../issues/001-public-static-files-bypass-authorization.md)).
A UUID in a path is an identifier, not a credential — it appears in histories, exports and
browser logs. The frontend resolves every `image_url` against the API origin (`getApiMediaUrl`), so
a hosted SPA on another origin still loads them; `<img>` requests carry cookies in no-cors mode,
so no `crossorigin` attribute is needed.

---

## 92. SSRF protection

**What it does.** Any URL the server fetches on a client's behalf — an article to import, an image
to proxy, a web result to read — is refused if it resolves to private, loopback, link-local,
reserved or multicast space, **including after a redirect**, and the connection is pinned to the
address that was vetted.

**Where.** [`core/net_safety.py`](../../backend/app/core/net_safety.py)
(`resolves_to_private_address`, `safe_send_async` / `safe_send_sync`,
`_PinnedAsyncNetworkBackend`, `safe_async_transport`), used by `image_service.py`,
`article_extraction.py`, the scraping clients.

**How it works.** Three layers. (1) Resolve the host and reject if *any* answer is unsafe; DNS
failure is unsafe too. (2) Never `follow_redirects=True`: `safe_send_*` walks the redirect chain
itself, re-running the check on every hop, with a shorter hop budget than httpx's default — a
host that legitimately resolves public can 302 to `169.254.169.254` otherwise; malformed
`Location` headers are treated as unsafe rather than crashing. (3) A custom httpcore network
backend resolves **at connection time** and hands the socket the vetted IP while httpcore keeps
the hostname for `Host` and TLS SNI — closing the DNS-rebinding window between "checked" and
"connected" ([012](../issues/012-ssrf-check-has-a-dns-rebinding-window.md)). Verified live: a
private host, loopback, and a public host redirecting to loopback are all refused; HTTPS to a
real host still works through the pinned transport.

---

## 93. Bounded image proxy

**What it does.** Web images in answers are fetched by the server (so the browser never talks to
a hostile or hotlink-protected host), with hard limits on size, time and content type, and cached
by content hash.

**Where.** `image_service.py::fetch_image_via_proxy` (`MAX_IMAGE_BYTES`, `REQUEST_TIMEOUT`,
`ALLOWED_CONTENT_TYPES`).

**How it works.** The response is **streamed** and counted as it arrives; a declared
`Content-Length` over the cap short-circuits, and an undeclared body is cut off at the cap rather
than buffered whole ([011](../issues/011-image-proxy-limit-is-checked-after-buffering.md)).
Content-addressed filenames dedupe naturally. Best-effort: a failure never breaks the answer.

---

## 94. Security headers

**What it does.** Every response carries `X-Content-Type-Options: nosniff` (no MIME-guessing a
served file into an executable type), `X-Frame-Options: SAMEORIGIN` (the app renders its own PDFs
inline, but no third-party site may frame it), `Referrer-Policy: strict-origin-when-cross-origin`
(paper titles and conversation ids stay out of the `Referer` when a reader clicks an external
citation). Raw article snapshots add `Content-Security-Policy: script-src 'none'; object-src
'none'` on top of their sanitisation.

**Where.** [`core/security.py::SecurityHeadersMiddleware`](../../backend/app/core/security.py),
`documents.py::_raw_html_headers`.
