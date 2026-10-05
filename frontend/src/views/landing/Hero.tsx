import { m, useReducedMotion } from 'motion/react';
import { HERO, LINKS, NAVIGATION, SCENE_COPY } from '../../landing/content';
import { Pressable, usePauseWhenHidden, useTilt } from '../../motion';
import { playful } from '../../motion/springs';
import { useMediaQuery } from './useMediaQuery';

function PageLines({ arabic = false, index = 0, animateHighlights }: { arabic?: boolean; index?: number; animateHighlights: boolean }) {
  const heights = index === 1 ? [78, 54, 92, 67, 44] : [65, 88, 48, 76, 58];
  return (
    <span className={`hero-page-lines${arabic ? ' is-arabic' : ''}`} aria-hidden="true">
      {heights.map((width, line) => (
        <span className="hero-page-line" key={line} style={{ width: `${width}%` }}>
          {line === 1 && (
            <m.i
              className="hero-highlight"
              animate={animateHighlights ? { scaleX: [0, 1] } : undefined}
              transition={animateHighlights ? { duration: 2.8, repeat: Infinity, ease: 'easeInOut', delay: index * 0.3 } : undefined}
            />
          )}
        </span>
      ))}
    </span>
  );
}

export function Hero({
  signedIn,
  onRequestAuth,
  onOpenLibrary,
}: {
  signedIn: boolean;
  onRequestAuth: (mode: 'login' | 'signup', opener: HTMLElement) => void;
  onOpenLibrary: () => void;
}) {
  const tilt = useTilt(8);
  const visible = usePauseWhenHidden(tilt.ref);
  const reducedMotion = useReducedMotion();
  const smallScreen = useMediaQuery('(max-width: 640px)');
  const tiltEnabled = !smallScreen && !reducedMotion;
  const pageTitles = [HERO.paperOneTitle, HERO.paperTwoTitle];

  return (
    <section id="hero" className="landing-hero" aria-labelledby="hero-title">
      <div className="landing-hero-copy">
        <p className="landing-eyebrow"><span className="eyebrow-mark" />{HERO.eyebrow}</p>
        <h1 id="hero-title">
          {HERO.titleBefore}<span className="hero-accent-word">{HERO.titleAccent}
            <svg className="hero-underline" viewBox="0 0 110 14" aria-hidden="true">
              <m.path d="M3 10 C30 2, 77 3, 106 9" pathLength="1" initial={reducedMotion ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={reducedMotion ? { duration: 0 } : { duration: 0.7, delay: 0.45, ease: 'easeOut' }} />
            </svg>
          </span>{HERO.titleAfter}
        </h1>
        <p className="landing-hero-subtitle">{HERO.subtitle}</p>
        <div className="landing-hero-actions">
          {signedIn ? (
            <Pressable className="landing-primary-cta" onClick={onOpenLibrary}>{NAVIGATION.openLibrary}</Pressable>
          ) : (
            <Pressable className="landing-primary-cta" onClick={(event) => onRequestAuth('signup', event.currentTarget)}>{HERO.primaryCta}</Pressable>
          )}
          <Pressable as="a" className="landing-secondary-cta" href={LINKS.repo} target="_blank" rel="noopener noreferrer">{HERO.secondaryCta} <span aria-hidden="true">↗</span></Pressable>
        </div>
      <p className="landing-hero-caption"><span aria-hidden="true">✳</span> {HERO.caption}</p>
      </div>

      <div className="hero-art" role="img" aria-label={HERO.artDescription}>
        <div className="hero-art-grain" aria-hidden="true" />
        <m.div
          ref={tilt.ref}
          className="hero-paper-stage"
          style={tiltEnabled ? tilt.style : undefined}
          onPointerMove={tiltEnabled ? tilt.onPointerMove : undefined}
          onPointerLeave={tiltEnabled ? tilt.onPointerLeave : undefined}
        >
          <m.article
            className="hero-paper hero-paper-back"
            aria-hidden="true"
            animate={reducedMotion ? undefined : visible ? { y: [0, -9, 0] } : { y: 0 }}
            transition={!reducedMotion && visible ? { duration: 6.8, repeat: Infinity, ease: 'easeInOut', delay: 0.7 } : undefined}
            whileHover={reducedMotion ? undefined : { x: -15, rotate: -16, transition: playful }}
          >
            <span className="hero-paper-kicker">{pageTitles[1]}</span>
            <span className="hero-arabic-sample" dir="rtl" lang="ar">{HERO.arabicSample}</span>
            <PageLines arabic index={1} animateHighlights={visible && !reducedMotion} />
          </m.article>
          <m.article
            className="hero-paper hero-paper-far"
            aria-hidden="true"
            animate={reducedMotion ? undefined : visible ? { y: [0, -8, 0] } : { y: 0 }}
            transition={!reducedMotion && visible ? { duration: 7.1, repeat: Infinity, ease: 'easeInOut', delay: 0.35 } : undefined}
            whileHover={reducedMotion ? undefined : { x: -12, rotate: -13, transition: playful }}
          >
            <span className="hero-paper-kicker">{HERO.paperThreeTitle}</span>
            <span className="hero-paper-meta">{HERO.paperThreeMeta}</span>
            <PageLines index={1} animateHighlights={visible && !reducedMotion} />
          </m.article>
          <m.article
            className="hero-paper hero-paper-front"
            aria-hidden="true"
            animate={reducedMotion ? undefined : visible ? { y: [0, -11, 0] } : { y: 0 }}
            transition={!reducedMotion && visible ? { duration: 6.2, repeat: Infinity, ease: 'easeInOut' } : undefined}
            whileHover={reducedMotion ? undefined : { x: 12, rotate: 8, transition: playful }}
          >
            <span className="hero-paper-kicker">{pageTitles[0]}</span>
            <span className="hero-paper-meta">{HERO.paperOneMeta}</span>
            <PageLines animateHighlights={visible && !reducedMotion} />
            <span className="hero-page-stamp"><span>{HERO.pageNumber}</span><small>{HERO.pageLabel}</small></span>
          </m.article>
          <m.div className="hero-highlight-note" aria-hidden="true" animate={reducedMotion ? undefined : { y: visible ? [0, -4, 0] : 0 }} transition={visible && !reducedMotion ? { duration: 4.8, repeat: Infinity, ease: 'easeInOut' } : undefined}>
            <span className="highlight-note-pin" />
            <span>{SCENE_COPY.heroNote}</span>
            <span className="note-rule" />
          </m.div>
          <div className="hero-ink-orbit" aria-hidden="true"><span /><span /><span /></div>
        </m.div>
        <p className="hero-art-caption"><span className="caption-dot" />{HERO.artCaption}</p>
      </div>
    </section>
  );
}
