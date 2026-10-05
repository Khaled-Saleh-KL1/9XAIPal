# Library + shared UI motion (PR 2 of 3): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Bring the owner's chosen **Playful** motion to the library and every shared overlay: buttons, dialogs, menus, the lightbox, the raw-files panel, toasts, route changes, library cards and the processing overlay.

**Architecture:** Reuse the PR 1 building blocks in `src/motion/` (`Pressable`, `Sheet`, `Reveal`, `Stagger`, the springs, `usePauseWhenHidden`). Overlays migrate onto `Sheet`, or onto `AnimatePresence` + `m.*` where `Sheet` doesn't fit. Library cards get `layout` + `AnimatePresence` for reflow, delete and arrival. Route changes get an **enter-only** page transition.

**Spec:** `docs/superpowers/specs/2026-10-04-landing-and-motion-design.md`, §3 "Per screen": Everywhere, Library, Processing overlay. The reader and desk are PR 3. Do not touch `ArticleReader` text, `BookReadingView`, `PdfViewer`, `DeskView`, `StickyNote`, `PaperPicker` or `MarginaliaPanel` in this PR, except for the toast (Task 5).

## Global Constraints

- Every PR 1 global constraint still applies:
  - frontend only;
  - `m.*` only, never the `motion.*` namespace;
  - tokens only for colors;
  - reduced motion means no transforms, and opacity fades of 150ms or less;
  - loops use `usePauseWhenHidden`;
  - animations never block input;
  - no em dashes in UI copy;
  - 360px with no horizontal scroll;
  - `npx vitest run` and `npm run build` green.
- **Owner decision "Full Motion everywhere":** every `<button>` in LibraryView, App.tsx (`UploadKindModal`), UserMenu, ShelfGroups, ConfirmDialog and ExportWizard becomes `Pressable`, or a motion component with equivalent behaviour. Use `intensity="calm"` for small icon or row actions (`CardActions`, `.shelf-option`) and `playful` for primary controls. Keep every `aria-*`, `title`, `disabled`, `type` and class name.
- **The library poll re-renders cards every 2.5s** while anything is processing (LibraryView:136-153; `metaToPaper` rebuilds the objects). Entrance animations must run only when a card first mounts, never on a re-render. Never key motion by anything except `paper.id`.
- **`.lightbox-backdrop`** must stay on the lightbox's backdrop element for its whole visible lifetime: `ChatPane.tsx:144` queries it.
- Remove a CSS keyframe or transition only once nothing uses it anymore. `marg-fade` and `marg-slide` are still used by reader and desk surfaces, so keep them.

## Rulings (orchestrator)

1. **Route transitions are enter-only.** Old routes unmount synchronously, exactly as today, and only the incoming view animates in. Do not use `AnimatePresence` across routes. App.tsx relies on synchronous unmount: `jumpTo`/`readingAnchor` are consumed on mount, `viewingPdf` is nulled before `setRoute`, the hash-sync effect and `navGenRef`, the lazy `PdfViewer` under `Suspense`, and the App.gate tests' text assertions. If this ruling is wrong, all it costs is the outgoing page not animating out.
2. **ProcessingOverlay tests render without `MotionRoot`.** Wrap their render in `MotionRoot`, which is a test-harness change only, and change no assertion. Step text must not get exit animations, because the old string must leave the DOM immediately.
3. **ConfirmDialog keeps its behaviour on `Sheet`:**
   - Enter confirms;
   - Escape cancels;
   - focus starts on the confirm button;
   - focus returns to the opener when it closes.
   Remove its window-level Escape listener so Escape isn't handled twice. Keep Enter-to-confirm, but only while the dialog is open and the focused element isn't a text input or textarea.

## Review Focus

1. **The 2.5s library poll:** cards must not replay their entrance or "drop-in" on every poll tick. Test: re-render the library with fresh objects for the same ids, and no entrance animation is triggered (see the Task 3 test).
2. **Delete, then quickly delete or filter again:** a card exiting via `AnimatePresence` must not block clicks on its neighbours, and must not come back if the poll returns before the exit finishes. Test: delete removes the card even when a refresh with the old list lands during the exit.
3. **Nested Escape:** the lightbox opened on top of a `Sheet`, or the UserMenu open while a confirm appears. Escape closes only the top-most layer. Test: open the confirm, press Escape, and only the confirm closes.
4. **Keyboard users on converted dialogs:** UploadKindModal, ExportWizard and ShelfPanel currently have no focus trap. After the change, focus moves in on open, Tab is trapped, and focus returns to the opener on close. Test: open UploadKindModal from its button, Tab cycles inside, and Escape returns focus.
5. **Reduced motion:** route changes, the dialogs and the library all still work, with no transforms. Test: with the reduced-motion mock, delete and filter still work and no transform style is present on the cards.

---

### Task 1: Pressable everywhere in the shared chrome

**Files:** `src/views/LibraryView.tsx`, `src/App.tsx` (UploadKindModal), `src/components/UserMenu.tsx`, `src/components/ShelfGroups.tsx`, `src/components/ConfirmDialog.tsx`, `src/components/ExportWizard.tsx`, plus tests.

- [ ] **Step 1 (failing tests):** add `src/components/pressableAdoption.test.tsx`. Render `UserMenuInline` (signed in, with a mocked `useAuth`) and `ConfirmProvider` (open a confirm through its hook), both inside `MotionRoot`. Assert that their buttons carry the `motion-pressable` class (or whatever marker `Pressable` puts on its element; check `src/motion/Pressable.tsx`), that they still have their accessible names, and that clicking still fires the action. Then run it and watch it fail.
- [ ] **Step 2:** Convert the buttons:
  - primary controls (Upload/Import, the UploadKindModal tiles, confirm go/cancel, export run/cancel, Sign out/About): `playful`;
  - `CardActions`, shelf options and row-level icon buttons: `calm`.
  - Delete the CSS hover transforms these replace, and only those (e.g. `.paper-actions button` transform, if present).
- [ ] **Step 3:** Run the whole suite and the build, then commit with `feat(motion): playful Pressable across library and shared chrome`.

### Task 2: Overlays onto Sheet / AnimatePresence

**Files:** `App.tsx` (UploadKindModal), `components/ConfirmDialog.tsx`, `components/ExportWizard.tsx`, `views/LibraryView.tsx` (ShelfPanel, ~1140), `components/UserMenu.tsx`, `components/ImageLightbox.tsx`, `views/RawFilesPanel.tsx`, `index.css`, plus tests.

**Behaviour:**
- **UploadKindModal, ConfirmDialog, ExportWizard and ShelfPanel** render inside `Sheet`, with `labelledBy` set to their title, `initialFocusRef` set to their current autofocus target, and `returnFocusRef` set to the opener. Their card styling (`.confirm-card` etc.) stays, and `Sheet` provides the backdrop and motion.
- **ExportWizard steps** crossfade and slide inside the sheet (`AnimatePresence mode="wait"`, keyed by step, using `gentle`).
- **UserMenu popover:** `AnimatePresence`. It springs open from its top-right origin with `scale 0.85→1`, `y -6→0` and `playful`, then shrinks out with `calm`. It stays a portal, and its outside-click, Escape, resize and scroll closing is unchanged.
- **ImageLightbox:** `AnimatePresence`. The backdrop is an `m.div` that **keeps the class `lightbox-backdrop`**, and fades. The image zooms from 0.92 with `playful`. The exit uses `calm`.
- **RawFilesPanel:** a right slide-over, `x: 100% → 0` with `gentle`, plus a backdrop fade. Add `role="dialog"`, `aria-modal` and a label, plus a focus trap: reuse the trap logic from `Sheet` by extracting a small `useFocusTrap` hook in `src/motion/` if needed. Escape closes it.
- **CSS:** delete `lightbox-fade`/`lightbox-rise` and their usages once nothing references them. Delete the confirm card's keyframe usage. Keep `marg-*`.

- [ ] **Step 1 (failing tests):**
  - **UploadKindModal:** open it from the upload button, check that it's a `dialog` with a name and that focus is inside, Tab-cycle, then press Escape: it closes and focus returns to the opener.
  - **ConfirmDialog:** opening it focuses confirm. Enter resolves `true`, and Escape resolves `false`, exactly once.
  - **Nested Escape:** open a confirm while UserMenu is open, press Escape, and only the confirm closes.
  - **ImageLightbox:** after opening, an element with class `lightbox-backdrop` exists. After Escape it's gone, checked with `waitFor`.
  - **RawFilesPanel:** it's a labelled dialog, Escape closes it, and focus returns.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run the suite and the build, then commit with `feat(motion): springy, accessible overlays (Sheet for dialogs, animated menu, lightbox, files panel)`.

### Task 3: Library cards: entrance, 3D hover, reflow, delete, arrival

**Files:** `views/LibraryView.tsx` (`PaperCard` ~912, `PaperRow` ~1068, grid/list containers, delete ~259-272, the poll ~136-157), `index.css` (`.paper-card` transition and hover, 90-106), plus tests.

**Behaviour:**
- **Initial load:** the cards that are on screen when the library first renders after loading fly in staggered (at most 12, using `Stagger`/`StaggerItem` variants, `y 18→0`, `scale .96→1`, `playful`). Later polls never replay this. Keep a `seenIds` ref: only ids added after the first load count as "new".
- **New upload arrival:** a card whose id is new since the last render drops in. It starts at `y -40`, `rotate -4`, `scale .9` and lands with `playful`, plus a brief accent glow ring that fades out over 0.8s.
- **Grid hover:** the cover swings open in 3D, using `rotateY -30°`, `rotateZ -3°`, a lift of `y -8` and a deeper shadow. Set `transform-origin` to the left edge and `perspective` on the parent.
- **Card press:** the jelly from `Pressable`.
- **Hover actions:** `.paper-actions` stays reachable by keyboard (`focus-within`).
- **List rows (`PaperRow`):** hover nudges the row with `x +4` and tints its background. No 3D effect.
- **Filtering and search:** cards carry `layout="position"` inside a `LayoutGroup`, so the survivors glide into their new places while removed cards shrink out (`AnimatePresence mode="popLayout"`, exit `scale .8`, `opacity 0`, `calm`).
- **Delete:** the card's exit is `scale .6`, `rotate 6`, `opacity 0`, and its neighbours close the gap through `layout`.
- **Grid ↔ list switch:** the container crossfades (`AnimatePresence mode="wait"` keyed by the layout).
- **Skeleton cards:** a soft shimmer, paused when hidden.
- **Reduced motion:** no transforms. Fades only.

- [ ] **Step 1 (failing tests)** in `src/views/LibraryView.motion.test.tsx`. Mock `../api` (`listPapers` etc.) and render inside `MotionRoot`:
  - Initial render shows all cards. A re-render via a poll with new object instances for the same ids keeps the same DOM nodes: capture a node, advance the fake timers past the poll, and the node `isSameNode`.
  - Delete, with a mocked confirm returning true and `deletePaper` resolving, removes the card (`waitFor` absent). Then a poll that still returns the deleted id during the exit must not resurrect the card after the exit completes. Note: if the server still lists it, document the current behaviour and assert parity with `main`.
  - Typing a filter removes the non-matching cards (`waitFor`), and clearing it restores them.
  - A new id appearing on a later poll renders a card with `data-arrival="true"`, a test hook for the drop-in, and cards that existed before don't have it.
  - With reduced motion, cards render with no inline `transform` other than `none`.
- [ ] **Step 2:** Implement. Replace the `.paper-card` CSS hover transform and transition with motion. Keep its non-motion styles.
- [ ] **Step 3:** Run the suite and the build, then commit with `feat(library): playful cards (stagger, 3D cover hover, reflow, delete, arrival)`.

### Task 4: Enter-only page transition

**Files:** `src/motion/PageTransition.tsx`, `src/App.tsx` (the route render, ~693-790), plus tests.

**Interface:** `PageTransition({ routeKey, children })`. It renders `m.div` with `key={routeKey}`, `initial={{ opacity: 0, x: 24, rotateY: -6 }}`, `animate={{ opacity: 1, x: 0, rotateY: 0 }}` and `transition={gentle}`, and `style={{ transformOrigin: 'left center', perspective: 1200 }}`. There's **no exit and no `AnimatePresence`** (Ruling 1). The wrapper must not break the views' `h-screen` layout (use `className="h-screen"` or `contents`, whichever keeps the layout identical). Reduced motion: opacity only, at most 150ms.

`routeKey` per route:
- `library` and `processing` share the key `library`, so the overlay doesn't re-animate the library;
- `reading:<paperId>`;
- `pdf:<paperId>`;
- `desk:<scope>:<page>`.

- [ ] **Step 1 (failing tests)**, added to `App.gate.test.tsx` or a new file: navigating from the library to the desk (or the reading mock) removes `LIBRARY` from the DOM **synchronously**, with no `waitFor`, and renders the new view. Switching between `library` and `processing` doesn't remount LibraryView: the mock counts its mounts.
- [ ] **Step 2:** Implement, run the suite and the build, then commit with `feat(motion): page-turn enter transition between screens`.

### Task 5: Toast and banner, plus the ProcessingOverlay

**Files:** `views/ArticleReader.tsx` (only the `.reader-toast` block, ~2283-2290), `views/LibraryView.tsx` (`.lib-notice` ~690), `views/ProcessingOverlay.tsx`, `views/ProcessingOverlay.test.tsx` (wrap the renders in `MotionRoot` only), `index.css` (`toast-in` 2707-2727).

**Behaviour:**
- **Toast:** an `AnimatePresence` bounce-in from below (`y 40→0`, `scale .9→1`, `playful`) and a drop-out on dismiss. The toast keeps the existing `translateX(-50%)` centring by passing `x: '-50%'` to motion. Delete `toast-in` once nothing uses it.
- **`.lib-notice`:** springs down from the top on appear and collapses on dismiss.
- **ProcessingOverlay:**
  - The card enters with `Reveal`-like motion.
  - The progress bar's fill animates `scaleX` with `gentle`, with `transform-origin` left, and a small wobble at its leading edge (a 6px blob that oscillates `scaleY`, paused when hidden).
  - The document card gets a slow highlight sweep, paused when hidden.
  - A step's badge pops (`scale .6→1`, `playful`) when it turns into a check.
  - Step **text** has no exit animations (Ruling 2).
  - `role="alert"`, `role="group"`, the Printed/Handwritten buttons' names and their enabled or disabled states are unchanged.
- [ ] **Step 1:** Wrap the ProcessingOverlay test renders in `MotionRoot` and run them: they must pass unchanged. Add a toast test in an `ArticleReader`-independent way: extract the toast into a `Toast` component in `src/components/Toast.tsx`, then test that it renders the message, × dismisses it, and the auto-dismiss timing (7s, or 9s for errors) is unchanged.
- [ ] **Step 2:** Implement, run the suite and the build, then commit with `feat(motion): bouncy toasts and banner, lively processing overlay`.

### Task 6: Verification and report

- [ ] Run `npx vitest run` 3 times (all green) and `npm run build`.
- [ ] Report the entry-chunk gzip delta against `main`. It must stay under +20 KB, since PR 2 adds no new library.
- [ ] Self-check:
  - no `motion.` namespace imports;
  - no transforms under reduced motion;
  - no reader or desk files changed except the toast extraction in ArticleReader;
  - unused keyframes removed.
- [ ] Browser check at 1440px and 360px against a dev server with a mocked or empty backend (the library shows its empty state or the auth gate). Use the vitest DOM for library interactions, and at least screenshot the overlays and the landing → sheet flow.
- [ ] Write the report: commits, tests before and after, the bundle delta, rulings applied, and anything unfinished.
