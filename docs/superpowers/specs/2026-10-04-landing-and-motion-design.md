# Landing page and Playful motion: design

Date: 2026-10-04
Status: approved in conversation (direction, motion level, flow, per-screen motion). Awaiting written-spec review.

## Goal

1. Give signed-out visitors a public landing page that explains what 9XAIPal is, says it is a free beta, links to the public GitHub repo, and introduces the author with a link to his portfolio.
2. Make the signed-in app feel alive. Things move on hover and when pressed, and screens animate in and out. The chosen level is **Playful**, but anything being read stays still.

Audience: students/researchers (product) and recruiters (author) equally.

## Decisions (from the owner)

| Topic | Decision |
|---|---|
| Visual direction | **A · Warm Paper**: the app's own cream background, terracotta accent (`--accent`), Newsreader serif headlines and Geist UI text. Floating paper pages and highlight sweeps. |
| Motion level | **Playful**: jelly press, springy overshoot, swaying sticky notes, pop-in messages. |
| Motion tech | **Full Motion everywhere**: the `motion` library (`motion/react`) drives all new and converted animations. |
| Quota | Wording only: "Free during the beta. Usage may be limited while we grow." No usage tracking. |
| Repo link | `https://github.com/Khaled-Saleh-KL1/9XAIPal` (public). |
| Portfolio link | `https://portfolio.kl1.site` |
| About text | Written by us from the portfolio: Khaled Saleh, AI Engineer. Arabic-aware NLP, RAG and multi-agent systems, built with FastAPI, LangGraph and Gemini. Builds production AI platforms at 9XAI / HTU. |
| Executor | Codex `gpt-6-luna` at max effort executes and reviews in multiple iterations. Claude orchestrates and checks in the browser. |

## Out of scope

- Backend or API changes, including saving sticky-note positions (stickies have no position field, so dragging is playful and the note springs back home).
- A dark theme.
- Real quota enforcement.
- Changing sign-in logic (email + password, `AuthContext`) or the capacity gate logic.

## 1. Flow and routing

Routing stays the custom hash router in `src/App.tsx` (`parseHash`/`writeHash`, `route` state).

- **Signed-out visitor:** the gate at `App.tsx` (~638-645) renders `LandingView` instead of `AuthView`. "Try the free beta" opens the auth sheet in signup mode, and "Sign in" opens it in login mode. The sheet is the existing `AuthView` form, wrapped in a motion `Sheet` (it slides up like paper, with a backdrop blur over the landing page). Escape or a backdrop click closes it. Its login and signup logic is unchanged. Successful auth goes through a page transition into the Library.
- **`#/welcome`:** shows `LandingView` to anyone. Signed-in visitors see "Open your library" instead of the sign-in buttons.
- **Signed-in visitor at `/` or `#/library`:** goes straight to the Library, with no landing page.
- **Account menu (`UserMenu`):** a new item, "About 9XAIPal", goes to `#/welcome`.
- **Waiting room (`WaitingRoomView`):** same logic, restyled in Warm Paper. The queue position uses a rolling-digit counter.
- **Beta badge:** a small `BETA` pill next to the logo in the Library and Desk headers (wherever `LogoMark` appears with the app name). Hovering or focusing it shows a tooltip: "Free during the beta. Usage may be limited while we grow."
- Production serving needs no change. Hash routes work under nginx `try_files` and under the Docker `StaticFiles` mode.

## 2. Landing page content (top to bottom)

All text and links live in `src/landing/content.ts`, so wording can be edited in one place.

1. **Top bar:**
   - left: `LogoMark` and "9XAIPal" with the BETA pill;
   - center: anchors to The journey and Built by;
   - right: "★ GitHub" (repo) and "Sign in".
   - It is sticky. After 24px of scroll it shrinks and gets a frosted (`backdrop-filter`) background.
2. **Hero:**
   - eyebrow "FREE BETA · ARABIC + ENGLISH";
   - headline "Read deeper. *Ask* your library." in Newsreader, with "Ask" in the accent color;
   - subtitle: "Upload papers, books and articles in English or Arabic. Read them beautifully, ask questions, and get answers with citations to the exact page."
   - buttons: "Try the free beta" (primary) and "View the code ↗" (repo);
   - visual: two or three floating paper pages, one with Arabic text set right-to-left. Highlights sweep across their lines. The pages tilt toward the pointer (`useTilt`) and spread apart on hover.
3. **The journey** (owner request: the page is a story that follows a student or researcher through their work, not a list of features):
   - **Persona switch** at the start of the journey: a two-option toggle, "I'm a student" / "I'm a researcher", default student. It only changes the example content in the chapters below (documents, questions, notes); layout and motion stay the same. The choice is kept for the visit (in memory only).
     - Student examples: lecture notes, a textbook chapter and an Arabic past exam, with a question like "Explain backpropagation simply, with the slide it comes from". The goal: exam ready.
     - Researcher examples: three papers (one in Arabic), with a question like "Which of these papers report results on Arabic OCR, and how do they compare?". The goal: literature review ready.
   - **Progress rail:** a thin vertical line on the left (desktop) or a top progress bar (mobile) with one dot per chapter. The dots fill as you scroll, and clicking a dot scrolls to its chapter. Driven by `useScroll`.
   - **Chapters**, each a full-height scene that animates as it scrolls into view and is tied to scroll where noted. Each has a small label ("Chapter 1 · The pile"), a serif title, one sentence, and the scene:
     1. **The pile.** Scattered documents (English and Arabic, PDFs and scans) drift on the page. As you scroll they gather and drop onto a neat shelf, landing one after another. Copy: "Too many PDFs, two languages, one deadline."
     2. **Read.** One document opens into a clean reader page. Its figure and equation snap into place, and the Arabic page flows right to left. Copy: "Every page, figure and equation, readable, in Arabic or English."
     3. **Ask.** A question types itself into an ask box. The answer streams in word by word, citation chips pop in, and the cited passage on the page lights up. Copy: "Ask anything. Every answer shows exactly where it came from."
     4. **Collect.** The answer folds into a sticky note and flies onto a desk, where it joins other notes into a study. Copy: "Keep what matters. Build your study as you go."
     5. **Find again.** A search across the whole shelf in both languages: matching documents rise and the rest dim. Copy: "Find the idea again weeks later, in either language."
   - **Finale, "Your turn":** the persona's goal ("Exam ready." or "Literature review ready."), the primary "Try the free beta" button and the beta note wording.
   - Every scene is built from HTML/CSS/SVG (no video, no images over 30 KB). With reduced motion, each scene shows its final state with a fade only. On mobile (<900px) the scenes stack, and scroll-tied parts become simple in-view animations.
   - The six capabilities (cited answers, Arabic + English OCR, desk and notes, smart search, streaming, figures and equations) must each appear in at least one chapter.
4. **Beta note:** folded into the finale above.
5. **Built by:**
   - name, "AI Engineer", the 2-3 sentence bio above;
   - buttons "Portfolio ↗" (`https://portfolio.kl1.site`) and "GitHub repo ↗".
6. **Footer:** "© 2026 Khaled Saleh", the same links, and "Made with care in Amman."

All external links use `target="_blank" rel="noopener noreferrer"`. The page must work at 360px width with a 16px side gutter and no horizontal scroll.

## 3. Motion system

### Building blocks (`src/motion/`)

| Module | Responsibility |
|---|---|
| `springs.ts` | Named presets. `playful` is an overshooting spring (stiffness ~400, damping ~17). `gentle` is a soft spring for panels and sheets. `calm` is a short tween (≤200ms) used in the reader and as the reduced-motion fallback. Also holds the jelly keyframes for press. |
| `MotionRoot.tsx` | Wraps the app in `LazyMotion` (features loaded asynchronously, `domMax` for drag and layout) and `MotionConfig reducedMotion="user"`. Uses `m.*` components, not `motion.*`, so the bundle stays small. |
| `Pressable.tsx` | A polymorphic button or link. Hover lifts it (y −2…−4, scale ~1.04–1.08, slight rotate). Press plays the jelly squash. Keeps every native button prop, `disabled`, focus ring and keyboard activation. |
| `Reveal.tsx` / `Stagger.tsx` | Enter animations when an element first scrolls into view (`whileInView`, `once: true`), and staggered children. |
| `PageTransition.tsx` | `AnimatePresence mode="popLayout"` keyed by route. The new screen slides in like a turning page (x and rotateY with a small skew). The old one fades out. It must not remount the lazy `PdfViewer` twice. |
| `Sheet.tsx` | Modal or drawer with a spring in and a shrink out. Includes focus trap, Escape and backdrop close, and `aria-modal`. Used by the auth sheet, upload modal, confirm dialog, export wizard and menus. |
| `useTilt.ts` | Tilts an element toward the pointer (motion values plus springs, max about 8°). Does nothing on touch devices and under reduced motion. |
| `usePauseWhenHidden.ts` | Pauses endless loops (sticky sway, hero float, highlight sweeps) when the element is off-screen (`useInView`) or the tab is hidden (`visibilitychange`). |

### Global rules

- **Reduced motion:** with `prefers-reduced-motion: reduce`, transforms are dropped and only opacity fades (≤150ms) remain (`MotionConfig reducedMotion="user"` plus the `calm` preset). The hand-written CSS keyframes in `index.css` also get a `@media (prefers-reduced-motion: reduce)` guard.
- **Reading stays still:** no hover, bounce or sway effects on the text of any document. That covers `ArticleReader`/`ArticleBlock` content, `PdfViewer` pages, book text, markdown answer bodies after they arrive, and note text being edited. The surfaces around them (panels, buttons, chips, message arrival) may move.
- **Never block input:** animations are interruptible, clicks work mid-animation, and nothing waits for an animation before handling an action.
- **Performance:**
  - animate only `transform` and `opacity` (plus `filter` on the hero);
  - lists over 40 items stagger only the first 12;
  - no layout animation on the reader;
  - endless loops use `usePauseWhenHidden`.
- **Existing CSS keyframes** in `index.css` stay where they already work (spinners, progress stripes). The animations being converted move to motion, and their old CSS is deleted (no dead keyframes left behind).

### Per screen (Playful)

- **Everywhere:**
  - buttons use `Pressable`;
  - menus, modals and toasts use `Sheet`, or spring in and bounce;
  - route changes use `PageTransition`.
- **Library:**
  - cards fly in staggered on load;
  - hovering a book swings its cover open in 3D (rotateY about −30°, rotateZ about −3°, lifted), and pressing it plays the jelly;
  - shelves reflow with `layout` animation when filtered or searched;
  - a deleted card shrinks out and its neighbours close the gap (`AnimatePresence` plus `layout`);
  - a new upload drops onto the shelf with a bounce.
- **Processing overlay:** the progress bar wobbles at its leading edge and the document card gets a highlight sweep. The `ProcessingOverlay.test.tsx` behaviour stays the same.
- **Reader:**
  - side panels (marginalia, evidence, chat pane) slide in and out with `gentle`;
  - chat messages pop in (scale 0.6→1 with a slight rotate);
  - streamed answer text fades in per chunk, and the final text never moves;
  - citation chips use a playful hover;
  - the document itself stays still.
- **Desk:**
  - sticky notes sway gently (paused when hidden) and wobble on hover;
  - they can be dragged and thrown (`drag`, `dragElastic`, `dragSnapToOrigin`) and spring back home with a bounce;
  - note and deck cards flip in on enter;
  - study cards tilt toward the pointer (`useTilt`).
- **Auth sheet:**
  - inputs shake on a failed login;
  - the submit button squishes, then morphs into a spinner while the request runs.
- **Waiting room:** a rolling-digit queue counter and a gently floating paper illustration.

## 4. Delivery

Three PRs. Each one is implemented and reviewed in multiple iterations by Codex Luna (max). Before the next PR starts, each is merged after green CI and a browser check, following the merge rules (CI green, server idle, Deploy success).

1. **PR 1, foundation and landing:**
   - add `motion`;
   - `src/motion/*`;
   - `LandingView` and `content.ts`;
   - auth sheet and the routing changes;
   - `#/welcome`;
   - the "About 9XAIPal" menu item;
   - waiting room restyle;
   - BETA badge.
2. **PR 2, library and shared UI:**
   - `Pressable` across buttons;
   - `Sheet` for modals, menus and confirm;
   - toasts;
   - `PageTransition`;
   - library cards, shelves, delete and upload drop;
   - processing overlay.
3. **PR 3, reader and desk:**
   - reader panels and chat message entrance;
   - streamed text fade;
   - citation chips;
   - stickies (sway, wobble, drag and throw);
   - note and deck cards;
   - study card tilt.

## 5. Testing and acceptance

Automated tests (Vitest + Testing Library, with `matchMedia` and `IntersectionObserver` mocked in test setup):

- A signed-out user sees `LandingView`, not the auth form.
- Clicking "Sign in" opens the sheet in login mode, and "Try the free beta" opens it in signup mode. Escape closes it.
- A successful login from the sheet renders the Library.
- A signed-in user at `/` gets the Library. At `#/welcome` they get the landing page with "Open your library".
- The repo and portfolio links have the exact hrefs above, plus `target="_blank"` and `rel` containing `noopener`.
- The BETA badge tooltip text appears on hover and on focus.
- With reduced motion on, `Pressable` and `Sheet` render and work. The test asserts no transform-based variant is applied, via `MotionConfig`.
- `Pressable` keeps button semantics: Enter and Space activate it, and `disabled` blocks clicks.
- A sticky note can be dragged and returns home, and no API call is made.
- All existing frontend tests still pass. `npm run build` passes (`tsc` + vite).

Manual or browser checks after each deploy (Claude, in the browser):

- hover and press on every converted control;
- landing at 360px, 768px and 1440px;
- reduced motion with the OS setting on;
- tab hidden, then loops paused;
- keyboard-only navigation through the landing page and the sheet.

Acceptance:

- Lighthouse on the landing page: Performance ≥ 90, Accessibility ≥ 95.
- No horizontal scroll at 360px.
- No console errors.
- The JS added to the initial load (gzipped) is reported in the PR and stays under 45 KB.

## Follow-up (after this project ships, owner request)

Reprocess every document of every user so all chunks carry contextual descriptions. If English chunks don't get the contextual descriptions yet, add them for English first. MinerU extraction itself stays unchanged. Tracked in memory `reprocess-all-after-frontend`.
