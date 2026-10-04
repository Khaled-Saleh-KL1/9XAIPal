# Landing page + motion foundation (PR 1 of 3): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the shared motion building blocks, the public Warm Paper landing page, the auth sheet, the `#/welcome` route, the restyled waiting room and the BETA badge.

**Architecture:** Add the `motion` library (`motion/react`) behind `LazyMotion` + `MotionConfig reducedMotion="user"`, with a small set of shared building blocks in `src/motion/`. The landing page is a new view rendered by the existing auth gate in `App.tsx` for signed-out visitors, and by a new `#/welcome` hash for anyone. Sign-in logic (`AuthContext`) is untouched. The existing form moves into an `AuthForm` component shown inside a motion `Sheet`.

**Tech stack:** React 19, Vite 6, TypeScript 5.7, Tailwind 3 plus the hand-written `src/index.css` tokens, Vitest 5 + Testing Library (jsdom), `motion` 14.x.

**Spec:** `docs/superpowers/specs/2026-10-04-landing-and-motion-design.md`. Read it first. PR 2 (library + shared UI) and PR 3 (reader + desk) get their own plans after this one merges.

## Global Constraints

- Work only in `frontend/`. No backend or API changes. Don't touch `backend/`, the root `pyproject.toml`, or anything under `docs/handoffs/`.
- Repo link: exactly `https://github.com/Khaled-Saleh-KL1/9XAIPal`. Portfolio link: exactly `https://portfolio.kl1.site`.
- Quota wording: exactly `Free during the beta. Usage may be limited while we grow.`
- Every external link: `target="_blank" rel="noopener noreferrer"`.
- Colors only from the existing `:root` tokens in `src/index.css` (`--bg`, `--bg-2`, `--fg`, `--fg-2`, `--muted`, `--border`, `--accent`, `--accent-fg`, …). Fonts: Newsreader (serif, headlines), Geist (UI). Arabic sample text uses the existing font stack with `dir="rtl"` and `lang="ar"`.
- Motion: import from `motion/react`. Use `m.*` components inside `LazyMotion`. Never import the full `motion.*` component namespace, which defeats lazy loading. Animate only `transform`/`opacity` (and `filter` in the hero).
- Reduced motion: `MotionConfig reducedMotion="user"` at the root, with no transform animations when reduced. Infinite loops must use `usePauseWhenHidden`.
- Animations never block input or delay a click handler.
- 360px wide: 16px side gutter, no horizontal scroll.
- Copy rule: no em dashes in visible UI text (use commas, colons or periods).
- `npm run build` (tsc + vite) and `npx vitest run` must pass. Run them from `frontend/`.
- Verify every `motion` API you use against the installed package's own type definitions (`node_modules/motion/...d.ts`). If a name in this plan differs in v14, use the real one and note it in your report.

## Review Focus

1. **Signed-in user on `#/welcome` + hash sync.** The App's hash-sync effect rewrites unknown hashes to `#/library`, so the welcome view must not get bounced. Test: a signed-in user at `#/welcome` still sees the landing page after effects run.
2. **Deep link while signed out** (e.g. `#/paper/123`). After logging in through the sheet, the user must land where the hash points, exactly as before. The landing page must not rewrite the hash. Test: the hash is unchanged after the landing page renders signed out.
3. **Keyboard and screen reader on the sheet.** Focus moves into the sheet on open, Tab is trapped, and Escape closes and returns focus to the button that opened it. Test: open → first input focused → Escape → opener focused.
4. **Reduced motion.** With `prefers-reduced-motion: reduce`, all controls still work, nothing relies on an animation completing, and loops don't run. Test: the reduced-motion matchMedia mock renders `Pressable` and `Sheet` and both still work.
5. **Double submit / failed login.** Pressing "Log in" twice must not send two requests, and a failed login shakes the form and shows the server message. Test: the second click while submitting is ignored, and the error text is shown.

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/package.json` | modify | add `motion` dependency |
| `src/test/setup.ts` | modify | jsdom mocks: `matchMedia`, `IntersectionObserver`, `ResizeObserver`, `scrollTo` |
| `src/motion/springs.ts` | create | spring presets and jelly keyframes |
| `src/motion/MotionRoot.tsx` | create | `LazyMotion` + `MotionConfig` wrapper |
| `src/motion/Pressable.tsx` | create | playful button/link building block |
| `src/motion/Reveal.tsx` | create | `Reveal` + `Stagger`/`StaggerItem` scroll-in |
| `src/motion/Sheet.tsx` | create | modal sheet (focus trap, Escape, backdrop) |
| `src/motion/useTilt.ts` | create | pointer tilt hook |
| `src/motion/usePauseWhenHidden.ts` | create | off-screen or hidden-tab pause hook |
| `src/motion/index.ts` | create | barrel export |
| `src/motion/motion.test.tsx` | create | tests for the building blocks |
| `src/main.tsx` | modify | wrap the tree in `MotionRoot` |
| `src/landing/content.ts` | create | all landing copy and links |
| `src/views/LandingView.tsx` | create | page shell, top bar, auth-sheet state |
| `src/views/landing/Hero.tsx` | create | hero with floating paper pages |
| `src/views/landing/Features.tsx` | create | 6 feature cards |
| `src/views/landing/HowItWorks.tsx` | create | 4 steps with the travelling page |
| `src/views/landing/BetaNote.tsx` | create | quota wording card |
| `src/views/landing/BuiltBy.tsx` | create | author section |
| `src/views/landing/Footer.tsx` | create | footer |
| `src/views/landing/landing.css` | create | landing-only styles (imported by `LandingView`) |
| `src/views/LandingView.test.tsx` | create | landing content, links, sheet behaviour |
| `src/views/AuthForm.tsx` | create | the form body moved out of `AuthView`, plus `initialMode`, shake and morphing submit |
| `src/views/AuthView.tsx` | delete | replaced by `AuthForm` inside `Sheet` (check that nothing else imports it) |
| `src/views/AuthForm.test.tsx` | create | form behaviour |
| `src/lib/welcomeRoute.ts` | create | `isWelcomeHash()`, `WELCOME_HASH` |
| `src/App.tsx` | modify | gate: landing for signed-out, `#/welcome`, hash-sync guard |
| `src/App.gate.test.tsx` | create | routing and gating tests |
| `src/components/UserMenu.tsx` | modify | "About 9XAIPal" item |
| `src/components/BetaBadge.tsx` | create | BETA pill + tooltip |
| `src/components/BetaBadge.test.tsx` | create | tooltip tests |
| `src/views/LibraryView.tsx` | modify | badge next to the logo (line ~485) |
| `src/views/DeskView.tsx` | modify | badge next to the logo (line ~502) |
| `src/views/WaitingRoomView.tsx` | modify | Warm Paper restyle + `RollingNumber` |
| `src/motion/RollingNumber.tsx` | create | rolling-digit counter |
| `src/index.css` | modify | `prefers-reduced-motion` guard for the existing keyframes |

---

### Task 1: Motion building blocks

**Files:** `package.json`, `src/test/setup.ts`, `src/motion/*` (all except `RollingNumber.tsx`), `src/main.tsx`, `src/motion/motion.test.tsx`

**Interfaces (produces):**
- `springs.ts`: `export const playful`, `gentle`, `calm` (motion `Transition` objects) and `export const jellyPress` (keyframes for `whileTap`).
- `MotionRoot({ children })`
- `Pressable` props: `as?: 'button' | 'a'`, plus every native button/anchor prop, plus `intensity?: 'playful' | 'calm'` (default `'playful'`). It forwards its ref.
- `Reveal({ children, delay?, y?, className?, as? })`; `Stagger({ children, className?, gap? })`; `StaggerItem({ children, className? })`
- `Sheet({ open, onClose, labelledBy, children, initialFocusRef?, returnFocusRef? })`
- `useTilt(maxDeg = 8)` returns `{ ref, style, onPointerMove, onPointerLeave }`
- `usePauseWhenHidden(ref)` returns `boolean` (true means animate)

- [ ] **Step 1: Install.** Run `npm install motion@^14` in `frontend/`. Commit `package.json` and `package-lock.json` together with the code below.

- [ ] **Step 2: Test-environment mocks.** Append to `src/test/setup.ts`:

```ts
import { vi } from 'vitest';

// jsdom has no matchMedia; tests flip `reducedMotion` to exercise the fallback.
export const motionPrefs = { reducedMotion: false };
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? motionPrefs.reducedMotion : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

class IO {
  constructor(private cb: IntersectionObserverCallback) {}
  observe(el: Element) {
    this.cb([{ isIntersecting: true, target: el, intersectionRatio: 1 } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}
(globalThis as any).IntersectionObserver = IO;
(globalThis as any).ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
window.scrollTo = vi.fn() as any;
```

Also add `afterEach(() => { motionPrefs.reducedMotion = false; })`.

- [ ] **Step 3: Write the failing tests** in `src/motion/motion.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { MotionRoot, Pressable, Sheet } from './index';
import { motionPrefs } from '../test/setup';

describe('Pressable', () => {
  it('keeps button semantics: Enter and Space activate, disabled blocks', async () => {
    const onClick = vi.fn();
    render(<MotionRoot><Pressable onClick={onClick}>Go</Pressable><Pressable disabled onClick={onClick}>No</Pressable></MotionRoot>);
    const go = screen.getByRole('button', { name: 'Go' });
    go.focus();
    await userEvent.keyboard('{Enter}');
    await userEvent.keyboard(' ');
    expect(onClick).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it('renders an anchor with the given href when as="a"', () => {
    render(<MotionRoot><Pressable as="a" href="https://x.test" target="_blank" rel="noopener noreferrer">X</Pressable></MotionRoot>);
    expect(screen.getByRole('link', { name: 'X' })).toHaveAttribute('href', 'https://x.test');
  });

  it('still works with reduced motion', async () => {
    motionPrefs.reducedMotion = true;
    const onClick = vi.fn();
    render(<MotionRoot><Pressable onClick={onClick}>Go</Pressable></MotionRoot>);
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

function SheetHarness() {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLInputElement>(null);
  return (
    <MotionRoot>
      <button ref={opener} onClick={() => setOpen(true)}>Open</button>
      <Sheet open={open} onClose={() => setOpen(false)} labelledBy="t" initialFocusRef={first} returnFocusRef={opener}>
        <h2 id="t">Title</h2>
        <input ref={first} aria-label="first" />
        <button>Last</button>
      </Sheet>
    </MotionRoot>
  );
}

describe('Sheet', () => {
  it('opens as a labelled modal dialog, focuses the first field, closes on Escape and returns focus', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    const dialog = await screen.findByRole('dialog', { name: 'Title' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByLabelText('first')).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Open' })).toHaveFocus();
  });

  it('traps Tab inside the sheet', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    await screen.findByRole('dialog');
    screen.getByRole('button', { name: 'Last' }).focus();
    await userEvent.tab();
    expect(screen.getByLabelText('first')).toHaveFocus();
  });

  it('closes on backdrop click', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    await screen.findByRole('dialog');
    fireEvent.mouseDown(screen.getByTestId('sheet-backdrop'));
    fireEvent.click(screen.getByTestId('sheet-backdrop'));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
```

- [ ] **Step 4: Run them** with `npx vitest run src/motion`. Expected: FAIL, because `./index` doesn't exist yet.

- [ ] **Step 5: Implement.** Reference code for the smaller modules:

```ts
// src/motion/springs.ts
import type { Transition } from 'motion/react';
/** Overshooting spring used across the app (owner chose "Playful"). */
export const playful: Transition = { type: 'spring', stiffness: 400, damping: 17, mass: 0.9 };
/** Softer spring for panels, sheets and drawers. */
export const gentle: Transition = { type: 'spring', stiffness: 260, damping: 26 };
/** Short tween for reading surfaces and the reduced-motion path. */
export const calm: Transition = { type: 'tween', duration: 0.18, ease: 'easeOut' };
/** Jelly squash played while pressed. */
export const jellyPress = { scaleX: [1, 1.12, 0.94, 1.03, 1], scaleY: [1, 0.86, 1.06, 0.98, 1] };
```

```tsx
// src/motion/MotionRoot.tsx
import { LazyMotion, MotionConfig } from 'motion/react';
import type { ReactNode } from 'react';
// domMax (drag + layout) is loaded asynchronously so it stays out of the first paint.
const loadFeatures = () => import('motion/react').then((mod) => mod.domMax);
export function MotionRoot({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
```

`usePauseWhenHidden(ref)`: returns `useInView(ref, { margin: '100px' }) && !documentHidden`, where `documentHidden` is tracked with a `visibilitychange` listener.

`useTilt(maxDeg)`:
- Use `useMotionValue` for x and y in −0.5…0.5, mapped through `useTransform` to rotateY/rotateX of ±maxDeg and smoothed with `useSpring(gentle)`.
- Ignore events where `pointerType !== 'mouse'`, and do nothing when `useReducedMotion()` is true.
- Reset to 0 on leave.

`Pressable`:
- Renders `m.button` (default `type="button"`) or `m.a`.
- `whileHover`: `{ y: -3, scale: 1.05, rotate: -1 }` for playful and `{ y: -1 }` for calm, with transition `playful`.
- `whileTap`: `jellyPress` with `{ duration: 0.45 }` for playful, and `{ scale: 0.97 }` for calm.
- With `disabled`, pass no hover or tap props.
- With `useReducedMotion()`, pass no hover or tap transforms.

`Reveal` and `Stagger`:
- `Reveal` is `m.div` with `initial={{ opacity: 0, y }}`, `whileInView={{ opacity: 1, y: 0 }}`, `viewport={{ once: true, margin: '-60px' }}` and `transition={{ ...playful, delay }}`.
- `Stagger` uses variants with `staggerChildren: gap ?? 0.07`, applied to at most 12 children. `StaggerItem` consumes those variants.

`Sheet`:
- Portal to `document.body` with `AnimatePresence`.
- Backdrop: `m.div`, `data-testid="sheet-backdrop"`, fades in, `backdrop-filter: blur(6px)`, closes on click when the click started on the backdrop.
- Panel: `m.div role="dialog" aria-modal="true" aria-labelledby={labelledBy}`. It enters with `{ y: 40, opacity: 0, rotate: -1, scale: 0.96 } → { y: 0, opacity: 1, rotate: 0, scale: 1 }` using `playful`, and exits with `{ y: 24, opacity: 0, scale: 0.96 }` using `calm`.
- On open, focus `initialFocusRef` (or the first focusable element).
- A Tab/Shift+Tab trap cycles through the focusable elements inside the panel.
- Escape calls `onClose`.
- When it unmounts (exit complete), focus `returnFocusRef`.
- While open, lock body scroll (`document.body.style.overflow = 'hidden'`, restored on close).

`index.ts`: re-export everything.

- [ ] **Step 6:** Wrap `<App />` and `<ImageLightbox />` in `<MotionRoot>` inside the existing providers in `src/main.tsx`.

- [ ] **Step 7:** Run `npx vitest run src/motion`, then the whole suite with `npx vitest run`. Expected: all pass.

- [ ] **Step 8: Commit** with the message `feat(motion): shared motion building blocks (springs, Pressable, Sheet, Reveal, tilt)`.

### Task 2: Landing content and LandingView

**Files:** `src/landing/content.ts`, `src/views/LandingView.tsx`, `src/views/landing/*`, `src/views/LandingView.test.tsx`

**Interfaces:**
- **Consumes:** `Pressable`, `Reveal`, `Stagger`, `StaggerItem`, `useTilt` and `usePauseWhenHidden` from Task 1, and `LogoMark`.
- **Produces:**
  - `LandingView({ signedIn, onRequestAuth, onOpenLibrary })`, where `onRequestAuth(mode: 'login' | 'signup', opener: HTMLElement)` and `onOpenLibrary()`. Its CTA buttons call `onRequestAuth`, and the sheet itself is owned by the parent (Task 4).
  - `BetaBadge` is used here, so build it in this task; its own test is in Task 5. `BetaBadge()` renders the pill with its tooltip.

- [ ] **Step 1: `content.ts`.** Exact copy:

```ts
export const LINKS = {
  repo: 'https://github.com/Khaled-Saleh-KL1/9XAIPal',
  portfolio: 'https://portfolio.kl1.site',
} as const;

export const BETA_NOTE = 'Free during the beta. Usage may be limited while we grow.';

export const HERO = {
  eyebrow: 'FREE BETA · ARABIC + ENGLISH',
  titleBefore: 'Read deeper. ',
  titleAccent: 'Ask',
  titleAfter: ' your library.',
  subtitle:
    'Upload papers, books and articles in English or Arabic. Read them beautifully, ask questions, and get answers with citations to the exact page.',
  primaryCta: 'Try the free beta',
  secondaryCta: 'View the code',
  arabicSample: 'اقرأ بعمق، واسأل مكتبتك',
} as const;

export const FEATURES = [
  { key: 'cite', title: 'Answers with citations', body: 'Every answer points to the page and passage it came from, so you can check it in one click.' },
  { key: 'ocr', title: 'Arabic + English OCR', body: 'Scanned or born-digital, right-to-left or left-to-right: text, tables and figures come out clean.' },
  { key: 'desk', title: 'Study desk & sticky notes', body: 'Collect answers, pin notes and build a study across several documents.' },
  { key: 'search', title: 'Smart search', body: 'Find the idea, not just the word, across your whole library in both languages.' },
  { key: 'stream', title: 'Streaming answers', body: 'Answers write themselves as they think, no waiting for a wall of text.' },
  { key: 'figures', title: 'Figures & equations intact', body: 'Charts, diagrams and LaTeX math stay where they belong in the reader.' },
] as const;

export const STEPS = [
  { key: 'upload', title: 'Upload', body: 'Drop a PDF, a book or an article link.' },
  { key: 'read', title: 'Read', body: 'A calm reader with the original layout, figures and math.' },
  { key: 'ask', title: 'Ask', body: 'Ask in Arabic or English and get cited answers.' },
  { key: 'collect', title: 'Collect', body: 'Keep what matters on your desk as notes and studies.' },
] as const;

export const AUTHOR = {
  name: 'Khaled Saleh',
  role: 'AI Engineer',
  bio: [
    'I design and ship end-to-end intelligent systems, from Arabic-aware NLP pipelines to multi-agent orchestration, using FastAPI, LangGraph and Gemini.',
    'I build production AI platforms at 9XAI / HTU. 9XAIPal is my own project: a reading companion I wanted for myself, now open as a free beta.',
  ],
} as const;

export const FOOTER = { copyright: '© 2026 Khaled Saleh', tagline: 'Made with care in Amman.' } as const;
```

- [ ] **Step 2: Write the failing tests** in `src/views/LandingView.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { LandingView } from './LandingView';
import { LINKS, BETA_NOTE, FEATURES } from '../landing/content';

const renderLanding = (props: Partial<Parameters<typeof LandingView>[0]> = {}) =>
  render(<MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} {...props} /></MotionRoot>);

describe('LandingView', () => {
  it('shows the hero, all six features, the beta note and the author', () => {
    renderLanding();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Read deeper. Ask your library.');
    for (const f of FEATURES) expect(screen.getByRole('heading', { name: f.title })).toBeInTheDocument();
    expect(screen.getAllByText(BETA_NOTE).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: 'Khaled Saleh' })).toBeInTheDocument();
  });

  it('links to the repo and the portfolio in new tabs, safely', () => {
    renderLanding();
    const repoLinks = screen.getAllByRole('link').filter((a) => a.getAttribute('href') === LINKS.repo);
    const portfolio = screen.getAllByRole('link').filter((a) => a.getAttribute('href') === LINKS.portfolio);
    expect(repoLinks.length).toBeGreaterThanOrEqual(2); // top bar + hero (+ built-by/footer)
    expect(portfolio.length).toBeGreaterThanOrEqual(1);
    for (const a of [...repoLinks, ...portfolio]) {
      expect(a).toHaveAttribute('target', '_blank');
      expect(a.getAttribute('rel')).toContain('noopener');
    }
  });

  it('asks for signup from the primary CTA and login from the top bar', async () => {
    const onRequestAuth = vi.fn();
    renderLanding({ onRequestAuth });
    await userEvent.click(screen.getAllByRole('button', { name: /try the free beta/i })[0]);
    expect(onRequestAuth).toHaveBeenLastCalledWith('signup', expect.any(HTMLElement));
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /sign in/i }));
    expect(onRequestAuth).toHaveBeenLastCalledWith('login', expect.any(HTMLElement));
  });

  it('offers "Open your library" instead of sign-in when signed in', async () => {
    const onOpenLibrary = vi.fn();
    renderLanding({ signedIn: true, onOpenLibrary });
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: /sign in/i })).toBeNull();
    await userEvent.click(screen.getAllByRole('button', { name: /open your library/i })[0]);
    expect(onOpenLibrary).toHaveBeenCalled();
  });

  it('does not touch the URL hash', () => {
    window.history.replaceState(null, '', '#/paper/abc');
    renderLanding();
    expect(window.location.hash).toBe('#/paper/abc');
  });
});
```

- [ ] **Step 3:** Run `npx vitest run src/views/LandingView.test.tsx`. Expected: FAIL (module missing).

- [ ] **Step 4: Implement the page**, following spec §2 exactly.

**Page structure and the shell:**
- Landmarks: `<header role="banner">` top bar, `<main>`, `<footer>`. In-page anchors are `#features`, `#how`, `#built-by`.
- In-page anchors must not change `location.hash`, because the hash is the app router. Implement them as buttons that call `document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })`.
- The shell is a scroll container: `min-h-screen` with `overflow-y: auto` on the page element. The app's root may have `overflow:hidden`, so check `index.css` `body`/`#root` rules.
- The top bar:
  - shrinks from 64 to 52px and gets the frosted background (`backdrop-filter: blur(10px)`, `background: color-mix(in oklch, var(--bg) 78%, transparent)`) once `scrollY > 24`, using `useScroll` on the container plus `useMotionValueEvent`;
  - repo button text: "★ GitHub";
  - signed out shows a "Sign in" button; signed in shows "Open your library".

**Hero:**
- `h1` in Newsreader, clamp(40px, 7vw, 76px), with `titleAccent` in an `em` colored `var(--accent)`.
- The `em` underline draws in: an SVG path with `pathLength` animated 0→1 after 0.6s.
- Buttons:
  - primary: `Pressable`, "Try the free beta" when signed out, "Open your library" when signed in;
  - secondary: `Pressable as="a"` to the repo with the label "View the code ↗".
- Paper pages:
  - Three absolutely positioned cards: two English, made of line bars, and one Arabic, showing `arabicSample` with `dir="rtl" lang="ar"`.
  - Each card floats endlessly: `y: [0, -10, 0]`, 6-7s, with staggered delays. While `usePauseWhenHidden` is false, set `animate={{ y: 0 }}` instead of the loop.
  - Highlight bars sweep with `scaleX` 0→1, `transform-origin` left (right for Arabic), repeating.
  - The pages group uses `useTilt(8)`. On group hover the pages spread apart: each card's `x` and `rotate` animate outward with `playful`.
  - Below 640px the pages sit under the text, scaled to 0.8, and the tilt is off.

**Features:**
- A `Stagger` grid with 3 columns on desktop, 2 on tablet and 1 on mobile.
- Each card is `StaggerItem` with `whileHover={{ y: -6, rotate: -1 }}` (`playful`) and a 64px illustration (inline SVG or CSS) that animates while hovered or in view:
  - cite: a chip pops in;
  - ocr: Arabic and English letters swap;
  - desk: a sticky note wobbles;
  - search: a magnifier sweeps;
  - stream: words type in;
  - figures: a bar chart grows.
- Card headings are `h3`.

**How it works:**
- A two-column section from 900px wide: left is a sticky page illustration, right is the four step cards.
- `useScroll({ target: sectionRef, offset: ['start center', 'end center'] })` drives the page's `y` and `rotate` through `useTransform`, so it travels past the steps. The active step (index from `scrollYProgress`) is highlighted with the accent border.
- Below 900px: steps stack, each in a `Reveal`, with no sticky element.
- Step headings are `h3`; the section heading is `h2` "How it works".

**Beta note:** a paper card with `BETA_NOTE` and a primary CTA, which behaves the same as the hero CTA.

**Built by:**
- `h2` "Built by"; inside it, `h3` `AUTHOR.name`, the role and the two bio paragraphs.
- Buttons: `Pressable as="a"` "Portfolio ↗" → `LINKS.portfolio`, and "GitHub repo ↗" → `LINKS.repo`.
- A monogram "KS" in a circle that tilts toward the pointer (`useTilt`).

**Footer:** copyright, tagline, and the repo and portfolio links.

**BetaBadge** (`src/components/BetaBadge.tsx`):
- An accent-outlined pill reading "BETA" that springs in on mount with `scale 0 → 1` (`playful`).
- It is a `button type="button"` with `aria-describedby` pointing to the tooltip id.
- On hover or focus, the tooltip (`role="tooltip"`) appears with `BETA_NOTE`, springing up 6px. It hides on mouse leave, blur and Escape.

**`landing.css`:** layout rules only, plus colors from the tokens. Import it from `LandingView.tsx`.

- [ ] **Step 5:** Run the landing tests and the whole suite. Expected: PASS.

- [ ] **Step 6: Commit** with `feat(landing): Warm Paper landing page with playful motion`.

### Task 3: AuthForm (moved out of AuthView)

**Files:** create `src/views/AuthForm.tsx` and `src/views/AuthForm.test.tsx`; delete `src/views/AuthView.tsx` (first run `grep -rn "AuthView" src` and update every importer; only `App.tsx` should import it).

**Interfaces:**
- **Produces:** `AuthForm({ initialMode, titleId, firstFieldRef })`, where:
  - `initialMode`: `'login' | 'signup'`;
  - `titleId`: string, the id put on the title element so `Sheet` can label itself;
  - `firstFieldRef`: `RefObject<HTMLInputElement>` for the email input.
- **Behaviour:** identical login and signup logic to the current `AuthView` (`useAuth().login/signup`, the error message, the 8-character signup minimum, the optional display name, the mode toggle). The full-screen wrapper is removed; the card itself remains.

- [ ] **Step 1: Write the failing tests** (mock `../contexts/AuthContext`):

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MotionRoot } from '../motion';

const login = vi.fn();
const signup = vi.fn();
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ login, signup }) }));
import { AuthForm } from './AuthForm';

const renderForm = (mode: 'login' | 'signup') =>
  render(<MotionRoot><AuthForm initialMode={mode} titleId="auth-title" firstFieldRef={createRef()} /></MotionRoot>);

beforeEach(() => { login.mockReset(); signup.mockReset(); });

describe('AuthForm', () => {
  it('opens in the requested mode', () => {
    renderForm('signup');
    expect(screen.getByText('Create an account')).toHaveAttribute('id', 'auth-title');
    expect(screen.getByPlaceholderText('Display name (optional)')).toBeInTheDocument();
  });

  it('logs in with the typed credentials', async () => {
    login.mockResolvedValue(undefined);
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'secret123');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(login).toHaveBeenCalledWith('a@b.co', 'secret123');
  });

  it('ignores a second submit while the first is running', async () => {
    let resolve!: () => void;
    login.mockReturnValue(new Promise<void>((r) => { resolve = r; }));
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'secret123');
    const submit = screen.getByRole('button', { name: 'Log in' });
    await userEvent.click(submit);
    await userEvent.click(submit);
    expect(login).toHaveBeenCalledTimes(1);
    resolve();
  });

  it('shows the server error after a failed login', async () => {
    login.mockRejectedValue(new Error('Invalid email or password'));
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'wrongpass');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password');
  });
});
```

- [ ] **Step 2:** Run the tests. Expected: FAIL.

- [ ] **Step 3: Implement.** Move the form JSX from `AuthView.tsx`, then make these changes:
  - The error `div` gets `role="alert"`.
  - Return early from the submit handler while `submitting` is true.
  - The form is an `m.form` that shakes on each new error, using `useAnimate` or an `animate` keyed by an error counter with `x: [0, -10, 9, -6, 4, 0]` over 0.4s.
  - The submit button is a `Pressable`. While submitting, its label crossfades (`AnimatePresence mode="wait"`) to a 14px spinner plus "Please wait…", and the button keeps its width (min-width set from the first render).
  - The mode toggle crossfades the title and the display-name field (height animation, `gentle`).

- [ ] **Step 4:** Run the tests and the whole suite. Expected: PASS. The `App.tsx` import is fixed in Task 4, so commit Task 3 together with Task 4 if the build would break in between.

### Task 4: Gate, `#/welcome`, sheet wiring and the "About" menu item

**Files:** create `src/lib/welcomeRoute.ts` and `src/App.gate.test.tsx`; modify `src/App.tsx` and `src/components/UserMenu.tsx`.

**Interfaces:**
- **Produces:**
  - `WELCOME_HASH = '#/welcome'`;
  - `isWelcomeHash(hash = window.location.hash): boolean`;
  - `useWelcomeRoute(): [boolean, (open: boolean) => void]`, which tracks `hashchange` and `popstate`. Opening pushes `#/welcome`. Closing pushes `#/library`.

**Behaviour in `App.tsx`:**
- `const [welcome, setWelcome] = useWelcomeRoute();`
- The sheet state lives in App: `authSheet: { mode: 'login' | 'signup' } | null`, plus `authOpenerRef` holding the clicked element.
- Signed out (`!user`): render `<LandingView signedIn={false} onRequestAuth={...} onOpenLibrary={noop} />` and `<Sheet open={!!authSheet} ...><AuthForm initialMode={authSheet.mode} .../></Sheet>`. Once `user` becomes non-null, the gate falls through as today. The waiting room comes next if the user isn't admitted, and otherwise the routes.
- Signed in and `welcome` true: render `<LandingView signedIn onOpenLibrary={() => setWelcome(false)} onRequestAuth={noop} />`, and nothing else.
- **Hash-sync guard:** the existing sync effect must not run `writeHash` while `welcome` is true. Add `if (isWelcomeHash()) return;` at the top of that effect, and in the `onNavigate` listener, ignore the welcome hash: don't parse it as library. Otherwise the library effect overwrites `#/welcome` with `#/library`, which is Review Focus #1.
- Signed out: the landing page and the sheet never write the hash, so deep links like `#/paper/<id>` survive sign-in (Review Focus #2).

**`UserMenu`:** add a `menuitem` button "About 9XAIPal" above "Sign out". It closes the menu and sets `window.location.hash = '#/welcome'` (the `hashchange` event reaches `useWelcomeRoute`).

- [ ] **Step 1: Write the failing tests** in `src/App.gate.test.tsx`. Mock `./contexts/AuthContext` with a mutable `authState`, and mock the heavy views so App renders cheaply:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const authState: any = { user: null, loading: false, admitted: true, queuePosition: null, login: vi.fn(), signup: vi.fn(), logout: vi.fn(), refreshAdmission: vi.fn() };
vi.mock('./contexts/AuthContext', () => ({ useAuth: () => authState, AuthProvider: ({ children }: any) => children }));
vi.mock('./views/LibraryView', () => ({ LibraryView: () => <div>LIBRARY</div> }));
vi.mock('./views/WaitingRoomView', () => ({ WaitingRoomView: () => <div>WAITING</div> }));
// Add further vi.mock stubs here for any module App imports that calls the network on mount
// (check App.tsx imports; stub api.ts functions App calls in effects to resolve empty).
import { App } from './App';
import { MotionRoot } from './motion';

const renderApp = () => render(<MotionRoot><App /></MotionRoot>);

beforeEach(() => {
  authState.user = null; authState.admitted = true;
  window.history.replaceState(null, '', '#/library');
});

describe('App gate', () => {
  it('shows the landing page, not the auth form, to a signed-out visitor', () => {
    renderApp();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Read deeper.');
    expect(screen.queryByPlaceholderText('Email')).toBeNull();
  });

  it('opens the sheet in login mode from "Sign in" and closes it with Escape', async () => {
    renderApp();
    await userEvent.click(screen.getByRole('button', { name: /^sign in$/i }));
    expect(await screen.findByRole('dialog', { name: 'Welcome back' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens the sheet in signup mode from "Try the free beta"', async () => {
    renderApp();
    await userEvent.click(screen.getAllByRole('button', { name: /try the free beta/i })[0]);
    expect(await screen.findByRole('dialog', { name: 'Create an account' })).toBeInTheDocument();
  });

  it('keeps a deep link while signed out', () => {
    window.history.replaceState(null, '', '#/paper/abc');
    renderApp();
    expect(window.location.hash).toBe('#/paper/abc');
  });

  it('sends a signed-in user straight to the library', () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();
    expect(screen.getByText('LIBRARY')).toBeInTheDocument();
  });

  it('shows the landing page at #/welcome to a signed-in user and keeps the hash', async () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    window.history.replaceState(null, '', '#/welcome');
    renderApp();
    expect(await screen.findAllByRole('button', { name: /open your library/i })).not.toHaveLength(0);
    expect(window.location.hash).toBe('#/welcome');
    await userEvent.click(screen.getAllByRole('button', { name: /open your library/i })[0]);
    expect(await screen.findByText('LIBRARY')).toBeInTheDocument();
    expect(window.location.hash).toBe('#/library');
  });

  it('shows the waiting room to a signed-in but not admitted user', () => {
    authState.user = { id: 'u', email: 'a@b.co' }; authState.admitted = false;
    renderApp();
    expect(screen.getByText('WAITING')).toBeInTheDocument();
  });
});
```

Also add a test in a `UserMenu` test file: clicking "About 9XAIPal" sets `location.hash` to `#/welcome`.

- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement as described above.
- [ ] **Step 4:** Run the tests, the whole suite, and `npm run build`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(app): landing page for visitors, auth sheet, #/welcome and About menu item` (include the Task 3 files if they weren't committed yet).

### Task 5: BETA badge in the app headers, waiting room restyle, reduced-motion CSS guard

**Files:** `src/components/BetaBadge.test.tsx`, `src/views/LibraryView.tsx` (~485), `src/views/DeskView.tsx` (~502), `src/views/WaitingRoomView.tsx`, `src/motion/RollingNumber.tsx`, `src/index.css`

- [ ] **Step 1: Write the failing tests:**

```tsx
// src/components/BetaBadge.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect } from 'vitest';
import { MotionRoot } from '../motion';
import { BetaBadge } from './BetaBadge';
import { BETA_NOTE } from '../landing/content';

it('shows the beta note on hover and on keyboard focus, hides on Escape', async () => {
  render(<MotionRoot><BetaBadge /></MotionRoot>);
  const badge = screen.getByRole('button', { name: /beta/i });
  await userEvent.hover(badge);
  expect(await screen.findByRole('tooltip')).toHaveTextContent(BETA_NOTE);
  await userEvent.unhover(badge);
  badge.focus();
  expect(await screen.findByRole('tooltip')).toBeInTheDocument();
  await userEvent.keyboard('{Escape}');
  await expect.poll(() => screen.queryByRole('tooltip')).toBeNull();
});
```

Also write a `RollingNumber` test: rendering `<RollingNumber value={12} />` exposes the text `12` to assistive technology (an `aria-label` or a visually-hidden span), and a rerender with `5` exposes `5`.

- [ ] **Step 2:** Run them. Expected: FAIL for `RollingNumber`; the badge test may already pass from Task 2, which is fine.

- [ ] **Step 3: Implement.**
  - **Headers:** place `<BetaBadge />` right after the "9XAIPal" text in `LibraryView`, and after the "Desk" title in `DeskView`. The header layout must not wrap at 360px (check the existing `min-w-0`/overflow comment near line 490).
  - **`RollingNumber`:** each digit is an `m.span` column that slides vertically to the new digit (`playful`), plus `aria-live="polite"` with the plain number as visually-hidden text.
  - **`WaitingRoomView`:** same logic, restyled as a Warm Paper card:
    - the radial cream background from the landing hero;
    - a floating paper illustration, paused when hidden;
    - the queue position through `RollingNumber`;
    - the card enters with `Reveal`.
  - **`index.css`:** add at the end:

```css
/* Respect the OS "reduce motion" setting for the hand-written keyframes too. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 4:** Run the whole suite and `npm run build`. Expected: PASS.

- [ ] **Step 5: Commit** with `feat(app): BETA badge, Warm Paper waiting room, reduced-motion guard`.

### Task 6: Verification and the PR report

- [ ] **Step 1:** In `frontend/`, run `npx vitest run` and `npm run build`. Both must pass, with zero TypeScript errors.
- [ ] **Step 2: Bundle report.** Compare `dist/assets/*.js` gzipped sizes against `main`:
  - run `git stash` or use a temp worktree of `origin/main`;
  - run `npm ci && npm run build`;
  - measure with `gzip -c file | wc -c`.
  - Report the delta for the entry chunk (the JS loaded on first paint). It must be under 45 KB gzipped (spec §5). If it's over, check that only `m.*` and `LazyMotion` are imported eagerly.
- [ ] **Step 3: Self-review checklist:**
  - no `motion.` namespace imports;
  - no hard-coded hex colors in the new TSX (CSS tokens only);
  - every external link has `rel="noopener noreferrer"`;
  - no em dashes in visible copy;
  - every endless loop uses `usePauseWhenHidden`;
  - nothing in the reader was changed.
- [ ] **Step 4:** Write the report: files changed, test count before and after, the bundle delta, and any `motion` v14 API differences from this plan.

---

## Self-review (plan author)

- **Spec coverage:**
  - §1 flow: Tasks 2 and 4.
  - §2 landing: Task 2.
  - §3 building blocks: Task 1. `PageTransition` is deferred to PR 2, where route transitions are wired, and is listed in the spec's PR 2 scope.
  - Auth sheet details: Task 3.
  - Waiting room and BETA: Task 5.
  - §5 tests for PR 1: Tasks 1-5. The sticky drag test belongs to PR 3.
- **Deliberate deviation from the "complete code in every step" rule:** the landing sections are visual work, so Task 2 Step 4 gives exact structure, copy, motion values and acceptance tests instead of full JSX. The executor is a strong model that is reviewed in iterations, and Claude checks the result in the browser after deploy.
- **Types:** `onRequestAuth(mode, opener)` and `AuthForm({ initialMode, titleId, firstFieldRef })` are used the same way in Tasks 2, 3 and 4.
