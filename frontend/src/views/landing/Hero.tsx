import { m, useReducedMotion } from 'motion/react';
import { HERO, LINKS, NAVIGATION } from '../../landing/content';
import { Pressable } from '../../motion';
import { InstallApp } from '../../pwa/InstallApp';
import { HeroIntro } from './HeroIntro';

export function Hero({
  signedIn,
  onRequestAuth,
  onOpenLibrary,
}: {
  signedIn: boolean;
  onRequestAuth: (mode: 'login' | 'signup', opener: HTMLElement) => void;
  onOpenLibrary: () => void;
}) {
  const reducedMotion = useReducedMotion();

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
        <InstallApp />
      <p className="landing-hero-caption"><span aria-hidden="true">✳</span> {HERO.caption}</p>
      </div>

      <div className="hero-art">
        <HeroIntro />
        <p className="hero-art-caption"><span className="caption-dot" />{HERO.artCaption}</p>
      </div>
    </section>
  );
}
