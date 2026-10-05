# Area 10 — Landing and shared motion (features 113–114)

> Part of the [feature catalogue](README.md). Each entry records the visible behaviour, its source, and the relevant implementation details.
>
> **Reflects code as of:** 2026-10-05 (`48cb8c6`).

---

## 113. Landing page, welcome hash, and sign-in sheet

**What it does.** The landing page is the signed-out entry screen. A signed-in reader can open the same page at `#/welcome`, browse the product story, then return to the library with **Open your library**. The page presents a five-chapter example journey with a student/researcher switch. ([`App.tsx`](../../frontend/src/App.tsx), [`welcomeRoute.ts`](../../frontend/src/lib/welcomeRoute.ts), [`LandingView.tsx`](../../frontend/src/views/LandingView.tsx))

**Where.** [`App.tsx`](../../frontend/src/App.tsx) chooses the landing, wait, and application views; [`welcomeRoute.ts`](../../frontend/src/lib/welcomeRoute.ts) synchronizes the welcome hash; [`LandingView.tsx`](../../frontend/src/views/LandingView.tsx) composes the page; [`content.ts`](../../frontend/src/landing/content.ts) holds its copy; [`Journey.tsx`](../../frontend/src/views/landing/Journey.tsx) implements the chapters and progress rail.

**How it works.**

1. `WELCOME_HASH` is `#/welcome`. The route hook initializes from the current hash and follows `hashchange` and `popstate`; its setter pushes either `#/welcome` or `#/library`. In `App`, signed-out users receive `LandingView`; a signed-in user on the welcome hash receives it with the library action enabled. After that, a signed-in but not-yet-admitted user sees the waiting room; otherwise the normal library, reader, or desk route renders. The signed-in user menu’s **About 9XAIPal** item sets the welcome hash. ([`welcomeRoute.ts`](../../frontend/src/lib/welcomeRoute.ts), [`App.tsx`](../../frontend/src/App.tsx), [`UserMenu.tsx`](../../frontend/src/components/UserMenu.tsx))
2. The page header links to **The journey** and **Built by**, and includes the GitHub link and either **Sign in** or **Open your library**. The hero’s **Try the free beta** action requests signup. The page copy and footer link to the project repository and portfolio; those links use a new tab and `noopener noreferrer`. ([`LandingView.tsx`](../../frontend/src/views/LandingView.tsx), [`Hero.tsx`](../../frontend/src/views/landing/Hero.tsx), [`BuiltBy.tsx`](../../frontend/src/views/landing/BuiltBy.tsx), [`Footer.tsx`](../../frontend/src/views/landing/Footer.tsx), [`content.ts`](../../frontend/src/landing/content.ts))
3. The journey has five ordered chapters: **The pile** (collect documents), **Read**, **Ask**, **Collect**, and **Find again**. The radio-group switch changes sample documents, question, answer, citations, and the closing goal for a student or researcher; the chapter sequence and scene mapping are shared. The student sample ends with “Exam ready.” and the researcher sample with “Literature review ready.” These are landing-page examples. ([`content.ts`](../../frontend/src/landing/content.ts), [`Journey.tsx`](../../frontend/src/views/landing/Journey.tsx))
4. The journey rail has one labelled button per chapter. It marks the active chapter with `aria-current="step"`; selecting a dot scrolls that chapter into view. The rail is fixed and vertical on wider screens and becomes sticky and horizontal at the small-screen breakpoint. Its scroll-scaled fills are omitted when reduced motion is requested. ([`Journey.tsx`](../../frontend/src/views/landing/Journey.tsx), [`landing.css`](../../frontend/src/views/landing/landing.css))
5. **Sign in** opens the auth sheet in login mode; **Try the free beta** opens it in signup mode. The sheet focuses the email field, traps focus, closes on Escape or a backdrop click, and returns focus to its opener. `AuthForm` switches between login and signup, calls the matching auth action, shows validation/request errors, and disables the submit control while the request is pending. ([`App.tsx`](../../frontend/src/App.tsx), [`AuthForm.tsx`](../../frontend/src/views/AuthForm.tsx), [`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx), [`useFocusTrap.ts`](../../frontend/src/motion/useFocusTrap.ts))
6. The **BETA** badge exposes the copy “Free during the beta. Usage may be limited while we grow.” on hover or keyboard focus; Escape closes the tooltip. ([`BetaBadge.tsx`](../../frontend/src/components/BetaBadge.tsx), [`content.ts`](../../frontend/src/landing/content.ts))

**Waiting room.** When a signed-in user has not been admitted, `App` shows `WaitingRoomView`. It refreshes admission every six seconds, displays a rolling queue number when a positive position is available, and shows a floating paper illustration. The illustration loop pauses when it is offscreen or the document is hidden, and is disabled for reduced motion. ([`App.tsx`](../../frontend/src/App.tsx), [`WaitingRoomView.tsx`](../../frontend/src/views/WaitingRoomView.tsx), [`RollingNumber.tsx`](../../frontend/src/motion/RollingNumber.tsx), [`usePauseWhenHidden.ts`](../../frontend/src/motion/usePauseWhenHidden.ts))

**See it.** Open `#/welcome`, use the student/researcher radio buttons, and scroll through the five chapters. In the signed-in menu, choose **About 9XAIPal** to return to that page.

---

## 114. Shared motion system and route entrance

**What it does.** A shared motion layer supplies button and link feedback, in-view reveals, sheets, pointer tilt, and a small set of spring/tween presets. It is used throughout the app chrome, library, reader, desk, dialogs, toasts, and processing screen. ([`MotionRoot.tsx`](../../frontend/src/motion/MotionRoot.tsx), [`motion/index.ts`](../../frontend/src/motion/index.ts))

**Where.** The root is mounted by [`AppProviders.tsx`](../../frontend/src/AppProviders.tsx). The public primitive exports are listed in [`motion/index.ts`](../../frontend/src/motion/index.ts); their implementations and presets are in the linked motion files below.

**How it works.**

- `MotionRoot` loads Motion’s `domMax` features asynchronously through `LazyMotion`, enables strict mode, and configures `MotionConfig` to follow the user’s reduced-motion preference. The shared exports include `Pressable`, `Reveal`, `Stagger`, `Sheet`, `PageTransition`, `useTilt`, `usePauseWhenHidden`, and the `playful`, `gentle`, `calm`, reduced-motion fade, and press presets. ([`MotionRoot.tsx`](../../frontend/src/motion/MotionRoot.tsx), [`motion/index.ts`](../../frontend/src/motion/index.ts), [`springs.ts`](../../frontend/src/motion/springs.ts))
- `Pressable` renders a native button or anchor with hover and press feedback; it omits those transforms when reduced motion is requested or a button is disabled. `Reveal` enters once when visible, while `Stagger` limits its animated children to the first twelve. ([`Pressable.tsx`](../../frontend/src/motion/Pressable.tsx), [`Reveal.tsx`](../../frontend/src/motion/Reveal.tsx))
- `Sheet` portals a labelled modal dialog to `document.body`. Its default backdrop and panel are centered; it includes focus trapping, Escape/backdrop dismissal, and focus restoration. ([`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx), [`motion.css`](../../frontend/src/motion/motion.css), [`useFocusTrap.ts`](../../frontend/src/motion/useFocusTrap.ts))
- `usePauseWhenHidden` returns false when its element is outside the viewport margin or the document is hidden. Components use it to stop repeating motion while the user cannot see it. ([`usePauseWhenHidden.ts`](../../frontend/src/motion/usePauseWhenHidden.ts))

**Where motion is adopted.**

| Surface | Examples in the code |
| --- | --- |
| App chrome | Landing navigation and calls to action use `Pressable`; the signed-in menu animates its popup; the beta badge animates its tooltip. ([`LandingView.tsx`](../../frontend/src/views/LandingView.tsx), [`UserMenu.tsx`](../../frontend/src/components/UserMenu.tsx), [`BetaBadge.tsx`](../../frontend/src/components/BetaBadge.tsx)) |
| Library | New paper cards enter, removed cards exit, filtered layouts reposition, and the cover opens on hover. ([`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx)) |
| Reader | The marginalia panel slides, evidence details disclose, and the book reader’s new chat messages enter through animated wrappers. ([`MarginaliaPanel.tsx`](../../frontend/src/views/MarginaliaPanel.tsx), [`EvidencePanel.tsx`](../../frontend/src/views/EvidencePanel.tsx), [`ChatPane.tsx`](../../frontend/src/views/ChatPane.tsx), [`BookReadingView.tsx`](../../frontend/src/views/BookReadingView.tsx)) |
| Desk | Study rail rows use pointer tilt; sticky notes use motion wrappers; note-wall cards animate their first entry/removal and repositioning. ([`DeskView.tsx`](../../frontend/src/views/DeskView.tsx), [`StickyNote.tsx`](../../frontend/src/views/StickyNote.tsx), [`NoteWall.tsx`](../../frontend/src/views/NoteWall.tsx)) |
| Dialogs and overlays | Auth and upload dialogs use `Sheet`; confirmation dialogs use the same primitive. ([`App.tsx`](../../frontend/src/App.tsx), [`ConfirmDialog.tsx`](../../frontend/src/components/ConfirmDialog.tsx), [`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx)) |
| Toasts | A toast enters and exits with `AnimatePresence`; dismiss is a `Pressable`. ([`Toast.tsx`](../../frontend/src/components/Toast.tsx)) |
| Processing | The processing card and progress fill animate from reported state; the document sweep and progress wobble pause when hidden and are disabled for reduced motion. ([`ProcessingOverlay.tsx`](../../frontend/src/views/ProcessingOverlay.tsx)) |

**Route entrance.** `App` keys `PageTransition` by the active surface (including paper, PDF, desk scope, and desk page). The keyed wrapper enters with opacity, horizontal movement, and a slight Y rotation; it has no exit variant or `AnimatePresence`, so a route change replaces the prior screen immediately while the new wrapper enters. Reduced motion uses an opacity-only fade. ([`App.tsx`](../../frontend/src/App.tsx), [`PageTransition.tsx`](../../frontend/src/motion/PageTransition.tsx), [`PageTransition.test.tsx`](../../frontend/src/motion/PageTransition.test.tsx))

**Reduced motion.** The root Motion configuration follows the system preference. In addition, `Pressable` removes hover/press transforms, `Reveal` renders without its entrance transform, and `PageTransition` and `Sheet` use opacity-only fades. Landing navigation switches from smooth to automatic scrolling and removes the scroll-scaled progress fill; global CSS shortens keyframe animations and transitions and disables smooth scrolling. ([`MotionRoot.tsx`](../../frontend/src/motion/MotionRoot.tsx), [`Pressable.tsx`](../../frontend/src/motion/Pressable.tsx), [`Reveal.tsx`](../../frontend/src/motion/Reveal.tsx), [`PageTransition.tsx`](../../frontend/src/motion/PageTransition.tsx), [`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx), [`LandingView.tsx`](../../frontend/src/views/LandingView.tsx), [`Journey.tsx`](../../frontend/src/views/landing/Journey.tsx), [`index.css`](../../frontend/src/index.css))
