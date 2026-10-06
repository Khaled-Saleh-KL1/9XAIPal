import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { HERO } from '../../landing/content';
import { usePauseWhenHidden } from '../../motion';
import { calm, gentle, playful } from '../../motion/springs';

/**
 * The landing hero's opening film: a drawn reader buried under a storm of
 * papers, until the 9 mark sweeps them onto a shelf and hands over the one
 * page they need. It plays once per visit, then settles into a quiet "paper
 * galaxy" idle loop. There is deliberately no replay control: anything that
 * appears when the film ends shifts the centred scene and reads as a jolt.
 *
 * ⚠ The scene is decoration with a text alternative (the wrapper's
 * aria-label). The headline beside it is visible from the first frame, so
 * nothing waits on the film. Reduced motion skips straight to the calm final
 * frame, and every loop pauses while the scene is off-screen or the tab is
 * hidden.
 */

export type IntroPhase = 'storm' | 'rescue' | 'relief' | 'idle';

/** How long each act lasts before the next one starts. */
export const INTRO_TIMINGS = { storm: 2600, rescue: 2000, relief: 2400 } as const;

/** Set once the film has played, so a reload in the same tab skips it. */
export const INTRO_PLAYED_KEY = '9xaipal.heroIntroPlayed';

function readPlayed(): boolean {
  try {
    return window.sessionStorage.getItem(INTRO_PLAYED_KEY) === '1';
  } catch {
    return false;
  }
}

function markPlayed() {
  try {
    window.sessionStorage.setItem(INTRO_PLAYED_KEY, '1');
  } catch {
    // Private mode or blocked storage: the film just plays again next time.
  }
}

/** Deterministic pseudo-random numbers, so the storm looks the same on every render. */
function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

type PaperKind = 'pdf' | 'arabic' | 'plain' | 'sticky' | 'book';

interface Paper {
  kind: PaperKind;
  start: { x: number; y: number; rotate: number };
  swirl: { x: number; y: number; rotate: number };
  pile: { x: number; y: number; rotate: number };
  shelf: { x: number; y: number };
}

const SHELF_Y = 132;
/** The reader is drawn a size up from the desk, anchored on the desk line. */
const READER_SCALE = 'translate(280 332) scale(1.2) translate(-280 -332)';
const KINDS: PaperKind[] = ['pdf', 'plain', 'arabic', 'sticky', 'pdf', 'book', 'plain', 'arabic', 'pdf', 'sticky', 'plain', 'book'];

const PAPERS: Paper[] = (() => {
  const rand = seeded(9);
  // Where the pile lands: a heap on the desk and spilling onto the floor
  // around the reader, never covering their face.
  const pileSpots = [
    [170, 314], [206, 320], [356, 316], [394, 312], [150, 406], [420, 402],
    [198, 412], [374, 414], [230, 312], [334, 310], [124, 420], [452, 420],
  ];
  return KINDS.map((kind, i) => {
    const [px, py] = pileSpots[i];
    return {
      kind,
      start: { x: 90 + rand() * 480, y: 20 + rand() * 40, rotate: -60 + rand() * 120 },
      swirl: { x: 80 + rand() * 440, y: 90 + rand() * 170, rotate: -180 + rand() * 360 },
      pile: { x: px, y: py, rotate: -38 + rand() * 76 },
      // A neat row standing on the shelf, left to right.
      shelf: { x: 404 + i * 13, y: SHELF_Y - 17 },
    };
  });
})();

const FLECKS = (() => {
  const rand = seeded(42);
  return Array.from({ length: 30 }, (_, i) => ({
    x: 14 + rand() * 572,
    y: 14 + rand() * 456,
    rotate: rand() * 360,
    size: 3 + rand() * 4,
    accent: i % 5 === 0,
    drift: 4 + rand() * 9,
    duration: 7 + rand() * 7,
    delay: rand() * 4,
  }));
})();

function PaperSheet({ kind }: { kind: PaperKind }) {
  if (kind === 'sticky') {
    return (
      <g>
        <rect x={-16} y={-16} width={32} height={32} rx={2} className="intro-sticky" />
        <path d="M-10 -5 h18 M-10 2 h14 M-10 9 h10" className="intro-ink-faint" />
      </g>
    );
  }
  if (kind === 'book') {
    return (
      <g>
        <rect x={-18} y={-23} width={36} height={46} rx={3} className="intro-book" />
        <path d="M-12 -23 v46" className="intro-ink-faint" />
        <path d="M-6 -12 h16 M-6 -6 h12" className="intro-book-title" />
      </g>
    );
  }
  const arabic = kind === 'arabic';
  return (
    <g>
      <rect x={-17} y={-22} width={34} height={44} rx={2.5} className="intro-paper" />
      {kind === 'pdf' && <rect x={-12} y={-17} width={13} height={6} rx={1.5} className="intro-tag" />}
      <path
        d={arabic
          ? 'M12 -6 h-22 M12 0 h-17 M12 6 h-22 M12 12 h-13'
          : 'M-12 -6 h22 M-12 0 h17 M-12 6 h22 M-12 12 h13'}
        className="intro-ink-faint"
      />
    </g>
  );
}

/** The drawn reader, slumped with their head in their hands. */
function FrustratedReader() {
  return (
    <g className="intro-ink">
      <path d="M260 292 q20 -9 40 0 l7 42 h-54 z" className="intro-shirt" />
      <path d="M280 236 c-15 0 -26 11 -26 26 c0 14 11 25 26 25 c14 0 25 -11 25 -25 c0 -15 -11 -26 -25 -26 z" className="intro-skin" />
      <path d="M256 255 q4 -21 24 -20 q21 -2 26 19 q-9 -9 -25 -8 q-15 -1 -25 9 z" className="intro-hair" />
      <path d="M267 262 l7 3 l-7 3 M293 262 l-7 3 l7 3" />
      <path d="M271 278 q4 -3 9 0 q4 3 9 0" />
      <path d="M262 298 q-17 -21 -2 -42 M300 298 q17 -21 2 -42" />
      <circle cx={261} cy={255} r={5.5} className="intro-skin" />
      <circle cx={299} cy={255} r={5.5} className="intro-skin" />
    </g>
  );
}

/** The same reader, sitting up and smiling over the one page they needed. */
function RelievedReader() {
  return (
    <g className="intro-ink">
      <path d="M260 280 q20 -9 40 0 l7 54 h-54 z" className="intro-shirt" />
      <path d="M280 222 c-15 0 -26 11 -26 26 c0 14 11 25 26 25 c14 0 25 -11 25 -25 c0 -15 -11 -26 -25 -26 z" className="intro-skin" />
      <path d="M256 241 q4 -21 24 -20 q21 -2 26 19 q-9 -9 -25 -8 q-15 -1 -25 9 z" className="intro-hair" />
      <path d="M268 247 q4 -5 8 0 M284 247 q4 -5 8 0" />
      <path d="M270 259 q10 9 20 0" />
      <circle cx={265} cy={255} r={3} className="intro-blush" />
      <circle cx={295} cy={255} r={3} className="intro-blush" />
      <path d="M262 288 q-12 22 4 33 M298 288 q12 22 -4 33" />
    </g>
  );
}

function Sparkle({ x, y, size }: { x: number; y: number; size: number }) {
  const s = size;
  return (
    <path
      d={`M${x} ${y - s} q${s * 0.18} ${s * 0.82} ${s} ${s} q${-s * 0.82} ${s * 0.18} ${-s} ${s} q${-s * 0.18} ${-s * 0.82} ${-s} ${-s} q${s * 0.82} ${-s * 0.18} ${s} ${-s} z`}
      className="intro-sparkle"
    />
  );
}

export function HeroIntro() {
  const reducedMotion = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const visible = usePauseWhenHidden(ref);
  const [phase, setPhase] = useState<IntroPhase>(() => (reducedMotion || readPlayed() ? 'idle' : 'storm'));

  // Reduced motion can resolve after the first render: jump to the calm frame.
  useEffect(() => {
    if (reducedMotion) setPhase('idle');
  }, [reducedMotion]);

  useEffect(() => {
    if (reducedMotion || phase === 'idle') return;
    const next: Record<Exclude<IntroPhase, 'idle'>, IntroPhase> = { storm: 'rescue', rescue: 'relief', relief: 'idle' };
    const timer = window.setTimeout(() => {
      const upcoming = next[phase];
      if (upcoming === 'idle') markPlayed();
      setPhase(upcoming);
    }, INTRO_TIMINGS[phase]);
    return () => window.clearTimeout(timer);
  }, [phase, reducedMotion]);

  const sorted = phase === 'rescue' || phase === 'relief' || phase === 'idle';
  const relieved = phase === 'relief' || phase === 'idle';
  const looping = visible && !reducedMotion;

  return (
    <div className="hero-intro" ref={ref} data-phase={phase}>
      <svg
        className="hero-intro-scene"
        viewBox="70 84 520 372"
        role="img"
        aria-label={HERO.introDescription}
      >
        <defs>
          {/* A faint wobble so the ink reads as hand-drawn, not vector-perfect. */}
          <filter id="intro-sketch" x="-5%" y="-5%" width="110%" height="110%">
            <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves={2} seed={3} />
            <feDisplacementMap in="SourceGraphic" scale={1.6} />
          </filter>
          <radialGradient id="intro-glow">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.32} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
          </radialGradient>
        </defs>

        {/* The paper galaxy: tiny flecks drifting like a slow starfield. */}
        <g aria-hidden="true">
          {FLECKS.map((f, i) => (
            <m.rect
              key={i}
              x={f.x}
              y={f.y}
              width={f.size}
              height={f.size * 1.3}
              rx={0.8}
              className={f.accent ? 'intro-fleck is-accent' : 'intro-fleck'}
              style={{ transformBox: 'fill-box', transformOrigin: 'center', rotate: f.rotate }}
              animate={looping ? { y: [0, -f.drift, 0], opacity: [0.35, 0.9, 0.35] } : { y: 0, opacity: 0.55 }}
              transition={looping ? { duration: f.duration, delay: f.delay, repeat: Infinity, ease: 'easeInOut' } : calm}
            />
          ))}
        </g>

        <g filter="url(#intro-sketch)" aria-hidden="true">
          <path d="M60 434 L540 434" className="intro-ink-faint" />
          {/* The shelf the papers will land on. */}
          <path d={`M392 ${SHELF_Y} L568 ${SHELF_Y} M402 ${SHELF_Y} l0 11 M558 ${SHELF_Y} l0 11`} className="intro-ink" />

          {/* The reader: slumped during the storm, upright once rescued. */}
          <g transform={READER_SCALE}>
          <AnimatePresence initial={false}>
            {relieved ? (
              <m.g
                key="relieved"
                initial={reducedMotion ? false : { opacity: 0, y: 8 }}
                animate={looping && phase === 'idle' ? { opacity: 1, y: [0, -1.5, 0] } : { opacity: 1, y: 0 }}
                exit={{ opacity: 0, transition: calm }}
                transition={{
                  opacity: { duration: 0.35 },
                  y: looping && phase === 'idle' ? { duration: 3.6, repeat: Infinity, ease: 'easeInOut' } : gentle,
                }}
              >
                <RelievedReader />
              </m.g>
            ) : (
              <m.g
                key="frustrated"
                style={{ transformBox: 'fill-box', transformOrigin: '50% 100%' }}
                initial={{ opacity: 0 }}
                animate={phase === 'storm' ? { opacity: 1, rotate: [-1.6, 1.6, -1.6] } : { opacity: 1, rotate: 0 }}
                exit={{ opacity: 0, transition: calm }}
                transition={{
                  // The fade-in plays once; only the tremble repeats. Sharing one
                  // repeating transition made the reader flicker in and out.
                  opacity: { duration: 0.25 },
                  rotate: phase === 'storm' ? { duration: 0.45, repeat: Infinity, ease: 'easeInOut' } : calm,
                }}
              >
                <FrustratedReader />
              </m.g>
            )}
          </AnimatePresence>
          </g>

          {/* The desk sits in front of the reader's lap. */}
          <path d="M136 332 L424 332" className="intro-desk-top" />
          <path d="M156 333 L151 434 M404 333 L409 434" className="intro-ink" />

          {/* Frustration: a scribble cloud and a little zigzag above the head. */}
          <g transform={READER_SCALE}>
          <AnimatePresence>
            {phase === 'storm' && (
              <m.g
                key="scribble"
                style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
                initial={{ opacity: 0, scale: 0.5 }}
                animate={{ opacity: 1, scale: [1, 1.12, 1], rotate: [0, 8, -6, 0] }}
                exit={{ opacity: 0, scale: 0.4, transition: calm }}
                transition={{ duration: 0.9, repeat: Infinity, ease: 'easeInOut' }}
              >
                <path d="M260 200 c4 -16 33 -16 37 0 c3 13 -27 15 -29 2 c-2 -11 20 -13 22 -2 c1 6 -9 7 -10 2" className="intro-ink" />
                <path d="M312 198 l7 -9 l-2 9 l7 -9" className="intro-ink" />
              </m.g>
            )}
          </AnimatePresence>
          </g>
        </g>

        {/* The papers: a storm, a pile, then a neat row on the shelf. */}
        <g aria-hidden="true">
          {PAPERS.map((p, i) => {
            const target = sorted
              ? { x: p.shelf.x, y: p.shelf.y, rotate: 0, scale: 0.42 }
              : { x: [p.start.x, p.swirl.x, p.pile.x], y: [p.start.y, p.swirl.y, p.pile.y], rotate: [p.start.rotate, p.swirl.rotate, p.pile.rotate], scale: 1 };
            const transition = sorted
              ? reducedMotion
                ? { duration: 0 }
                : { ...playful, delay: 0.35 + i * 0.07 }
              : { duration: 1.5 + (i % 4) * 0.12, delay: i * 0.11, ease: 'easeOut' as const, times: [0, 0.55, 1] };
            return (
              <m.g
                key={i}
                initial={reducedMotion || phase === 'idle' ? false : { x: p.start.x, y: p.start.y, rotate: p.start.rotate }}
                animate={target}
                transition={transition}
              >
                <PaperSheet kind={p.kind} />
              </m.g>
            );
          })}
        </g>

        {/* The 9 mark: pops in to the rescue, sends out a wave, then glows softly. */}
        <AnimatePresence>
          {sorted && (
            <m.g
              key="mark"
              aria-hidden="true"
              initial={reducedMotion ? false : { opacity: 0, scale: 0.3 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={reducedMotion ? { duration: 0 } : playful}
              style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
            >
              <m.circle
                cx={150}
                cy={128}
                r={44}
                fill="url(#intro-glow)"
                animate={looping ? { scale: [1, 1.18, 1], opacity: [0.7, 1, 0.7] } : { scale: 1, opacity: 0.8 }}
                transition={looping ? { duration: 3.2, repeat: Infinity, ease: 'easeInOut' } : calm}
                style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
              />
              {phase === 'rescue' && !reducedMotion && (
                <m.circle
                  cx={150}
                  cy={128}
                  r={20}
                  className="intro-wave"
                  initial={{ scale: 1, opacity: 0.7 }}
                  animate={{ scale: 26, opacity: 0 }}
                  transition={{ duration: 1.5, ease: 'easeOut' }}
                  style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
                />
              )}
              <rect x={131} y={109} width={38} height={38} rx={10} className="intro-mark" />
              <text x={150} y={136} textAnchor="middle" className="intro-mark-nine">9</text>
            </m.g>
          )}
        </AnimatePresence>

        {/* The one page that mattered, floating down into the reader's hands. */}
        <g transform={READER_SCALE}>
        <AnimatePresence>
          {relieved && (
            <m.g
              key="answer-page"
              aria-hidden="true"
              initial={reducedMotion || phase === 'idle' ? false : { x: 0, y: -150, rotate: -14, opacity: 0 }}
              animate={looping && phase === 'idle'
                ? { x: 0, y: 0, opacity: 1, rotate: [0, -2, 0] }
                : { x: 0, y: 0, rotate: 0, opacity: 1 }}
              transition={looping && phase === 'idle'
                ? { duration: 5, repeat: Infinity, ease: 'easeInOut' }
                : reducedMotion ? { duration: 0 } : { ...gentle, duration: 1.1 }}
              style={{ transformBox: 'fill-box', transformOrigin: '50% 100%' }}
            >
              <ellipse cx={280} cy={312} rx={34} ry={20} fill="url(#intro-glow)" />
              <rect x={261} y={295} width={38} height={28} rx={2.5} className="intro-answer-page" />
              <path d="M266 302 h24 M266 308 h18 M266 314 h22" className="intro-answer-lines" />
            </m.g>
          )}
        </AnimatePresence>

        </g>

        {/* Sparkles once the reader is relieved. */}
        <g transform={READER_SCALE}>
        <AnimatePresence>
          {relieved && (
            <m.g
              key="sparkles"
              aria-hidden="true"
              initial={reducedMotion || phase === 'idle' ? false : { opacity: 0, scale: 0.2 }}
              animate={looping ? { opacity: [0.5, 1, 0.5], scale: 1 } : { opacity: 1, scale: 1 }}
              transition={looping ? { duration: 2.4, repeat: Infinity, ease: 'easeInOut' } : playful}
              style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
            >
              <Sparkle x={236} y={214} size={7} />
              <Sparkle x={328} y={204} size={9} />
              <Sparkle x={318} y={240} size={5} />
            </m.g>
          )}
        </AnimatePresence>
        </g>
      </svg>

    </div>
  );
}
