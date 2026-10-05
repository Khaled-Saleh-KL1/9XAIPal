import { useCallback, useEffect, useRef, useState } from 'react';
import type { ComponentType, KeyboardEvent, RefObject } from 'react';
import { AnimatePresence, m, useInView, useMotionValue, useReducedMotion, useScroll, useTransform } from 'motion/react';
import { Pressable, Reveal } from '../../motion';
import { BETA_NOTE, CHAPTERS, HERO, JOURNEY as JOURNEY_COPY, NAVIGATION, PERSONAS, type Persona } from '../../landing/content';
import { AskScene } from './scenes/AskScene';
import { CollectScene } from './scenes/CollectScene';
import { FindScene } from './scenes/FindScene';
import { PileScene } from './scenes/PileScene';
import { ReadScene } from './scenes/ReadScene';
import { useMediaQuery } from './useMediaQuery';

type Chapter = (typeof CHAPTERS)[number];
type SceneProps = { progress: ReturnType<typeof useScroll>['scrollYProgress']; persona: Persona };
type ScrollContainerRef = RefObject<HTMLDivElement | null>;
const SCENES: Record<Chapter['key'], ComponentType<SceneProps>> = {
  pile: PileScene,
  read: ReadScene,
  ask: AskScene,
  collect: CollectScene,
  find: FindScene,
};

function PersonaSwitch({ persona, onChange }: { persona: Persona; onChange: (persona: Persona) => void }) {
  const refs = useRef<Record<Persona, HTMLButtonElement | null>>({ student: null, researcher: null });
  const options: Persona[] = ['student', 'researcher'];

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: Persona) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const next: Persona = current === 'student' ? 'researcher' : 'student';
    onChange(next);
    refs.current[next]?.focus();
  };

  return (
    <div className="persona-switch-wrap">
      <div className="persona-switch" role="radiogroup" aria-label={JOURNEY_COPY.chooseLabel}>
        {options.map((option) => (
          <Pressable
            key={option}
            ref={(node) => { refs.current[option] = node as HTMLButtonElement | null; }}
            className="persona-option"
            role="radio"
            aria-checked={persona === option}
            onClick={() => onChange(option)}
            onKeyDown={(event) => handleKeyDown(event, option)}
          >
            {persona === option && <m.span className="persona-active-pill" layoutId="persona-active-pill" transition={{ type: 'spring', stiffness: 380, damping: 30 }} />}
            <span>{PERSONAS[option].label}</span>
          </Pressable>
        ))}
      </div>
    </div>
  );
}

function JourneyProgress({ active, visible, progress }: { active: number; visible: boolean; progress: ReturnType<typeof useScroll>['scrollYProgress'] }) {
  const reducedMotion = useReducedMotion();
  const [entranceComplete, setEntranceComplete] = useState(false);
  const interactive = visible && entranceComplete;
  const entranceDuration = reducedMotion ? 150 : 240;
  const fillY = useTransform(progress, [0, 1], [0, 1]);
  const fillX = useTransform(progress, [0, 1], [0, 1]);

  useEffect(() => {
    if (!visible) {
      setEntranceComplete(false);
      return;
    }
    const timer = window.setTimeout(() => setEntranceComplete(true), entranceDuration);
    return () => window.clearTimeout(timer);
  }, [visible, entranceDuration]);

  return (
    <m.nav
      className="journey-progress"
      aria-label={JOURNEY_COPY.progressLabel}
      aria-hidden={!interactive}
      inert={!interactive}
      initial={{ opacity: 0, ...(!reducedMotion ? { x: -12 } : {}) }}
      animate={{ opacity: visible ? 1 : 0, ...(!reducedMotion ? { x: visible ? 0 : -12 } : {}) }}
      transition={{ duration: entranceDuration / 1000, ease: 'easeOut' }}
      style={{ pointerEvents: interactive ? 'auto' : 'none' }}
    >
      <div className="journey-progress-track" aria-hidden="true">
        {!reducedMotion && <m.span style={{ scaleY: fillY, originY: 0 }} />}
      </div>
      {!reducedMotion && <m.span className="journey-progress-mobile-fill" aria-hidden="true" style={{ scaleX: fillX, originX: 0 }} />}
      <div className="journey-progress-dots">
        {CHAPTERS.map((chapter, index) => (
          <Pressable
            key={chapter.key}
            className={`journey-progress-dot${active === index ? ' is-current' : ''}`}
            aria-label={chapter.label}
            aria-current={active === index ? 'step' : undefined}
            tabIndex={interactive ? 0 : -1}
            onClick={() => document.getElementById(`chapter-${chapter.key}`)?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' })}
          >
            <span />
          </Pressable>
        ))}
      </div>
    </m.nav>
  );
}

function ChapterSection({
  chapter,
  index,
  persona,
  small,
  scrollContainer,
  onActive,
}: {
  chapter: Chapter;
  index: number;
  persona: Persona;
  small: boolean;
  scrollContainer: ScrollContainerRef;
  onActive: (index: number) => void;
}) {
  const chapterRef = useRef<HTMLElement>(null);
  const pileSceneRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion();
  const inView = useInView(chapterRef, { root: scrollContainer, amount: 0.34 });
  const pileSceneInView = useInView(pileSceneRef, { root: scrollContainer, amount: 0.5 });
  const { scrollYProgress } = useScroll({ container: scrollContainer, target: chapterRef, offset: ['start end', 'end start'] });
  const mappedProgress = useTransform(scrollYProgress, (value) => small || reducedMotion ? 1 : value);
  const pileProgress = useMotionValue(reducedMotion ? 1 : 0);
  const sceneProgress = chapter.key === 'pile'
    ? (small || reducedMotion ? pileProgress : scrollYProgress)
    : mappedProgress;
  const Scene = SCENES[chapter.key];
  const scene = chapter.key === 'pile'
    ? <PileScene progress={sceneProgress} persona={persona} small={small} sceneRef={pileSceneRef} />
    : <Scene progress={sceneProgress} persona={persona} />;

  useEffect(() => {
    if (inView) onActive(index);
    if (chapter.key === 'pile' && small && pileSceneInView) pileProgress.set(1);
  }, [inView, pileSceneInView, index, onActive, chapter.key, small, pileProgress]);

  return (
    <section ref={chapterRef} id={`chapter-${chapter.key}`} className={`journey-chapter chapter-${chapter.key}`} aria-labelledby={`chapter-title-${chapter.key}`}>
      <div className="chapter-copy">
        <Reveal className="chapter-copy-inner">
          <p className="chapter-label"><span className="chapter-label-line" />{chapter.label}</p>
          <h3 id={`chapter-title-${chapter.key}`}>{chapter.title}</h3>
          <p className="chapter-body">{chapter.body}</p>
        </Reveal>
      </div>
      <div className="chapter-visual-column">
        <div className="journey-scene-sticky">
          {small ? <Reveal className="journey-scene-reveal">{scene}</Reveal> : scene}
        </div>
      </div>
    </section>
  );
}

export function Journey({
  scrollContainer,
  persona,
  onPersonaChange,
  signedIn,
  onRequestAuth,
  onOpenLibrary,
}: {
  scrollContainer: ScrollContainerRef;
  persona: Persona;
  onPersonaChange: (persona: Persona) => void;
  signedIn: boolean;
  onRequestAuth: (mode: 'login' | 'signup', opener: HTMLElement) => void;
  onOpenLibrary: () => void;
}) {
  const journeyRef = useRef<HTMLElement>(null);
  const inView = useInView(journeyRef, { root: scrollContainer, amount: 0.04 });
  const { scrollYProgress } = useScroll({ container: scrollContainer, target: journeyRef });
  const [active, setActive] = useState(0);
  const small = useMediaQuery('(max-width: 899px)');
  const setActiveChapter = useCallback((index: number) => setActive(index), []);

  return (
    <section ref={journeyRef} id="journey" className="landing-journey" aria-labelledby="journey-title">
      <JourneyProgress active={active} visible={inView} progress={scrollYProgress} />
      <div className="journey-intro">
        <Reveal className="journey-intro-copy">
          <p className="landing-eyebrow"><span className="eyebrow-mark" />{JOURNEY_COPY.eyebrow}</p>
          <h2 id="journey-title">{JOURNEY_COPY.eyebrow}</h2>
          <p className="journey-persona-prompt">{JOURNEY_COPY.personaPrompt}</p>
        </Reveal>
        <PersonaSwitch persona={persona} onChange={onPersonaChange} />
      </div>

      {CHAPTERS.map((chapter, index) => (
        <ChapterSection
          key={chapter.key}
          chapter={chapter}
          index={index}
          persona={persona}
          small={small}
          scrollContainer={scrollContainer}
          onActive={setActiveChapter}
        />
      ))}

      <div className="journey-finale">
        <Reveal className="finale-mark"><span /><span /><span /></Reveal>
        <Reveal>
          <h2>{JOURNEY_COPY.finaleTitle}</h2>
          <AnimatePresence mode="wait" initial={false}>
            <m.p className="finale-goal" key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>{PERSONAS[persona].goal}</m.p>
          </AnimatePresence>
          <p className="finale-beta-note">{BETA_NOTE}</p>
          {signedIn ? (
            <Pressable className="landing-primary-cta" onClick={onOpenLibrary}>{NAVIGATION.openLibrary}</Pressable>
          ) : (
            <Pressable className="landing-primary-cta" onClick={(event) => onRequestAuth('signup', event.currentTarget)}>{HERO.primaryCta}</Pressable>
          )}
        </Reveal>
        <span className="finale-side-note" aria-hidden="true">9XAIPal</span>
      </div>
    </section>
  );
}
