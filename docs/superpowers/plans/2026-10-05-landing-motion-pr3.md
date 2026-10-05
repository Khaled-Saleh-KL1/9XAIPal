# Reader + desk motion (PR 3 of 3): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Finish the owner's **Playful** motion on the reading and study surfaces (reader panels, chat messages, citation chips, the Desk, sticky notes, note and study cards) while **the text being read never moves**.

**Architecture:** Reuse `src/motion/` from PR 1 and PR 2 (`Pressable`, `Sheet`, `Reveal`, `Stagger`, `useTilt`, `usePauseWhenHidden`, `useFocusTrap`, the springs). Put motion only on **outer wrappers** and on surfaces around the text. Never put it on the reader's scroll containers, or on elements that existing code moves itself.

**Spec:** `docs/superpowers/specs/2026-10-04-landing-and-motion-design.md`, §3 "Reader" and "Desk".

## Global Constraints

- All PR 1 and PR 2 constraints apply:
  - frontend only;
  - `m.*` only;
  - tokens only for colors;
  - reduced motion means no transforms, and fades of 150ms or less;
  - loops use `usePauseWhenHidden`;
  - animations never block input;
  - no em dashes in UI copy;
  - 360px with no horizontal scroll;
  - tests and the build green.
- **Reading stays still.** Don't change any transform, opacity animation or hover effect on:
  - `ArticleReader` article blocks or `.reader-scroll`;
  - BookReadingView page text, PdfViewer pages or RawArticleViewer content;
  - rendered markdown bodies after they arrive.
  The existing `block-flash` jump highlight stays exactly as it is.
- **Never add transforms to an ancestor of `.reader-scroll`, `.picker-scrim`, or any `position: fixed` / absolutely-measured element.** Selection-pill math (ArticleReader:1181-1203), `layoutNotes` (680-760), BookReadingView's split-divider math (167-200) and scroll pinning (396-401, 544, 563) and BookNotes' layer all use `getBoundingClientRect`, and transformed ancestors break them.
- **Elements moved by existing code are off limits for motion transforms:**
  - the ArticleReader gutter cards (`NoteCard`/`PersonalNoteCard`/`DeckCard` inside `.note-slot`), because `layoutNotes` sets `style.transform` and `useCardDrag` (NoteChrome.tsx:66) sets transform and pointerEvents;
  - BookNotes icons (pointer capture);
  - `.ask-pill-group` (has its own `translateX(-50%)`);
  - DeckCard's CSS 3D flip.
  Motion may wrap their **content** (an inner element) for opacity or scale, never the element that code moves.

## Rulings (orchestrator)

1. **Streaming text:** no per-word or per-chunk fade on markdown. `ReactMarkdown` re-parses the whole message on every token, so animating chunks would remount nodes and flicker. Instead:
   - the streaming bubble pops in when it first appears;
   - a soft accent "ink" caret pulses at the end while streaming (paused when hidden);
   - when the stream finishes, the committed message must not re-animate. Give the pending and committed message the same stable key, or mark the committed one `initial={false}`.
   This changes the spec's "fade per chunk" wording. The cost, if the owner wanted per-word fades, is a later polish pass.
2. **StudyChat `Answer` builds `components={{ a(){} }}` inline** (StudyChat.tsx:58-100). That remounts every link, including `CitationRef`, which loses its open state and fetched text, on each paced update. Hoist and memoize it first, as a real bug fix with its own test.
3. **Sticky tilt:** `StickyNote` sets `style={{ transform: rotate(tilt) }}` on its `<article>` (StickyNote.tsx:134). Put the motion (sway, wobble, drag, enter and exit) on a **new outer `m.div` wrapper**, and leave the article's inline tilt alone.
4. **Gutter cards in ArticleReader** get nothing in this PR (see Global Constraints), except an opacity-only fade-in on an inner content wrapper for newly created cards.

## Review Focus

1. **Selecting text in the article** still shows the ask pill exactly at the selection after the PR, including after side panels open or close and after scrolling. Test: unit-test the pill position helper if it can be extracted; otherwise verify manually with the fixture and record the measurement.
2. **Dragging a sticky versus editing it.** While editing (textarea focused), drag is disabled and typing, selecting text and Cmd+Enter work. A click without moving opens the editor exactly as before. A drag never fires the edit or delete buttons. Test: pointer-move simulation with `fireEvent`; editing still commits on blur; delete still needs 2 clicks.
3. **Streaming answer completion:** there's no flash or re-animation when the pending bubble becomes the committed message. Citation chips inside a streaming StudyChat answer keep their open state across token updates (Ruling 2). Test: render the pending turn, open a citation chip, push another token, and the chip is still open.
4. **Escape layering in the reader:** with the lightbox open over the chat, Escape closes only the lightbox. With MarginaliaPanel open, Escape closes the panel and returns focus. PaperPicker on `Sheet` closes on Escape without also closing the Desk's other layers. Test: one of each.
5. **Reduced motion and big boards:** 40+ stickies or wall cards have no endless sway running off-screen (`usePauseWhenHidden`), and with reduced motion nothing sways or tilts. Test: the reduced-motion mock means no `animate` loop props; off-screen means paused.

---

### Task 1: Reader side panels and pickers

**Files:** `views/MarginaliaPanel.tsx`, `views/PaperPicker.tsx`, `views/EvidencePanel.tsx`, `views/BookReadingView.tsx` (mobile chat toggle only), `views/DeskView.tsx` (mobile rail only), `index.css`, plus tests.

- **MarginaliaPanel:**
  - `AnimatePresence`;
  - the scrim fades (`.marg-scrim` keeps its class);
  - the panel slides in from the right with `gentle` and slides out with `calm`;
  - add `aria-modal`, `useFocusTrap` and return focus to the opener.
  - Escape closes only when it is the top layer (keep the existing window listener semantics, but stop it firing when a higher layer such as the lightbox is open).
  - Delete the `marg-slide` usage. Keep `marg-fade` only while other users remain.
- **PaperPicker:** move onto `Sheet`, keeping its search autofocus, results and selection behaviour.
- **EvidencePanel disclosure:** the list reveals with opacity + `y 6→0` (`gentle`) on open. **No height animation.** The toggle chevron rotates with `playful`.
- **Mobile layouts:** the ChatPane toggle (BookReadingView:1239-1247) crossfades between panes. The Desk's mobile rail (`.rail.is-mobile-open`) springs in from the left with a fading backdrop.
- **Dead CSS:** delete `asst-panel`/`asst-scrim`/`asst-slide` (index.css ~2638-2662); nothing references them.
- [ ] **Tests first:**
  - MarginaliaPanel opens as a modal dialog, focuses search, traps Tab, closes on Escape and returns focus;
  - with a lightbox backdrop present, Escape doesn't close the panel;
  - PaperPicker is a labelled dialog via Sheet, and choosing a paper calls `onPick` and closes it;
  - the EvidencePanel toggle reveals the list and keeps `aria-expanded` correct.
- [ ] Implement, run the suite and the build, then commit with `feat(reader): springy side panels and picker (accessible, reading text untouched)`.

### Task 2: Chat messages, streaming, citation chips

**Files:** `views/ChatPane.tsx`, `views/StudyChat.tsx`, `views/NoteCard.tsx` (pending card only, through an inner wrapper), `views/CitationRef.tsx`, `views/BibCitationRef.tsx`, `index.css`, plus tests.

- **New messages (user or assistant) pop in:** `scale .6→1`, `rotate -3→0`, `y 12→0`, with `playful`. The initial history load renders with **no** entrance (`initial={false}` for the messages present on mount). Only messages appended later animate.
- **Streaming:** apply Ruling 1, the bubble pop-in plus a pulsing ink caret. No re-animation on commit.
- **StudyChat:** apply Ruling 2 (hoist the components) before anything else.
- **Autoscroll** (ChatPane:199-203) must still keep the newest text in view. Entrance transforms must not break the 120px at-bottom threshold, because transforms don't change layout height.
- **Citation chips (`.cite-chip`):**
  - `display: inline-block`;
  - hover: `y -1`, `scale 1.06` (`playful`);
  - press: the jelly;
  - the `.cite-peek` popover springs in (`scale .9→1`, `y 4→0`) and fades out.
  - Chips sit inside markdown text but are controls, not reading text, so they may move a little.
- **Composer:** the send button uses `Pressable`. The attach-image chip pops in.
- [ ] **Tests first:**
  - **StudyChat:** an open `CitationRef` stays open across a pending-answer update (this fails on the current code, per Ruling 2);
  - **ChatPane:** messages present on mount have no entrance animation props, a message appended later does, and the committed message after streaming doesn't re-enter (use a motion-props spy like `LibraryView.motion.test.tsx`);
  - **`CitationRef`:** clicking the chip toggles the peek, Escape and outside click behave as before, and its accessible name is unchanged;
  - **regression:** `api.streaming.test.ts` and `pacer.test.ts` still pass unchanged.
- [ ] Implement, run the suite and the build, then commit with `feat(chat): pop-in messages, ink caret while streaming, springy citation chips`.

### Task 3: Desk, stickies and cards

**Files:** `views/StickyNote.tsx`, `views/StickyBoard.tsx`, `views/NoteWall.tsx`, `views/DeskView.tsx` (study cards in the rail, add-note flow), `views/PersonalNoteCard.tsx` / `NoteCard.tsx` **only where rendered in NoteWall** (outer wrapper; check that `useCardDrag` isn't active there; if it is, leave the element alone), `index.css`, plus tests.

**Stickies** (outer `m.div` per Ruling 3):
- a gentle endless sway (`rotate ±1.5°`, 3-4s, staggered phase from the id hash), paused when hidden, off under reduced motion, and **paused while editing**;
- hover: wobble (`rotate` keyframes `[0,-4,3,-2,0]` over 0.5s) plus lift;
- **drag and throw:** `drag`, `dragElastic 0.6`, `dragSnapToOrigin`, `whileDrag={{ scale: 1.08, rotate: 4, zIndex: 10 }}`. It springs back home with a bounce. Disable drag while editing, and on the textarea and the buttons (use `dragListener` / `onPointerDownCapture` to ignore those targets). No API calls. A plain click (movement under 4px) behaves exactly as before.
- **new sticky (`addNote`):** pops in (`scale .5→1`, `rotate -8→tilt`, `playful`) with its textarea still focused;
- **delete:** exits shrinking and twirling (`scale .4`, `rotate 20`, `opacity 0`) via `AnimatePresence` in StickyBoard;
- **the list reflows** with `layout="position"`.
- **Collapse/expand of the board** (StickyBoard:36-52) crossfades.

**NoteWall cards:**
- staggered flip-in on first mount (`rotateX -70→0`, `opacity`, at most 12 staggered);
- filtering and search reflow (`layout="position"` + `AnimatePresence popLayout`);
- hover tilt toward the pointer (`useTilt(6)`) on the outer wrapper.
- The deterministic `tiltFor(id)` rotation stays on the inner element.

**Study cards (Desk rail):** `useTilt(6)` on hover plus the `Pressable` press. The active study gets a springy accent indicator (`layoutId`).

**DeckCard:** keep its CSS flip. Only add `@media (prefers-reduced-motion)` coverage if it's missing (it already checks `matchMedia`).

- [ ] **Tests first:**
  - **sticky:** dragging (pointerdown, move 40px, up) makes no API call (mock `api`) and leaves the note in place in the DOM order;
  - **sticky:** a click without movement enters edit mode, and while editing the drag listener is disabled (the drag props spy shows `drag={false}`);
  - **sticky:** delete still needs 2 clicks, and after the second the note exits (`waitFor` absent) and `deleteSticky` (or the existing callback) is called once;
  - `addNote` renders the new sticky with the textarea focused;
  - **reduced motion:** no sway or wobble animate props;
  - **NoteWall:** filtering removes non-matching cards, and cards on first mount have entrance props while re-renders don't replay them.
- [ ] Implement, run the suite and the build, then commit with `feat(desk): swaying, throwable stickies and lively note and study cards`.

### Task 4: Verification and report

- [ ] Run `npx vitest run` 3 times (all green) and `npm run build`. The entry-chunk gzip delta against main must be under +10 KB.
- [ ] **Browser check with the fixture** (extend `scratchpad/fe/fixture.mjs` locally if needed: one paper with chunks for the reader, a study, stickies and notes; never commit the fixture) at 1440px and 360px:
  - the ask pill sits at the selection;
  - the panels open and close;
  - chat streaming works if it can be simulated; otherwise rely on the tests;
  - sticky drag, throw and snap-back;
  - the wall reflows.
  Stop the servers afterwards.
- [ ] Self-check:
  - no transforms added to the forbidden elements or ancestors (grep the diff for `m.` on reader containers);
  - no `motion.` namespace imports;
  - dead CSS removed;
  - no files outside `frontend/` except the report.
- [ ] Write the report.
