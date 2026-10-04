import { useRef, useState } from 'react';
import { useMotionValueEvent, useScroll } from 'motion/react';
import { LogoMark } from '../components/LogoMark';
import { BetaBadge } from '../components/BetaBadge';
import { Pressable } from '../motion';
import { BUILT_BY_TITLE, LINKS, NAVIGATION, type Persona } from '../landing/content';
import { Hero } from './landing/Hero';
import { Journey } from './landing/Journey';
import { BuiltBy } from './landing/BuiltBy';
import { Footer } from './landing/Footer';
import './landing/landing.css';

export type LandingViewProps = {
  signedIn: boolean;
  onRequestAuth: (mode: 'login' | 'signup', opener: HTMLElement) => void;
  onOpenLibrary: () => void;
};

export function LandingView({ signedIn, onRequestAuth, onOpenLibrary }: LandingViewProps) {
  const [persona, setPersona] = useState<Persona>('student');
  const [compact, setCompact] = useState(false);
  const pageRef = useRef<HTMLDivElement>(null);
  const { scrollY } = useScroll({ container: pageRef });
  useMotionValueEvent(scrollY, 'change', (value) => setCompact(value > 24));

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const requestAuth = (mode: 'login' | 'signup', opener: HTMLElement) => onRequestAuth(mode, opener);

  return (
    <div ref={pageRef} className="landing-page">
      <header className={`landing-header${compact ? ' is-compact' : ''}`} role="banner">
        <div className="landing-topbar-inner">
          <div className="landing-brand-group">
            <Pressable className="landing-brand" onClick={() => scrollTo('hero')} aria-label={NAVIGATION.home}>
              <LogoMark />
              <span>9XAIPal</span>
            </Pressable>
            <BetaBadge />
          </div>
          <nav className="landing-topnav" aria-label={NAVIGATION.pageSectionsLabel}>
            <Pressable className="landing-nav-link" onClick={() => scrollTo('journey')}>{NAVIGATION.journey}</Pressable>
            <Pressable className="landing-nav-link" onClick={() => scrollTo('built-by')}>{BUILT_BY_TITLE}</Pressable>
          </nav>
          <div className="landing-top-actions">
            <Pressable as="a" className="landing-github-link" href={LINKS.repo} target="_blank" rel="noopener noreferrer">{NAVIGATION.github}</Pressable>
            {signedIn ? (
              <Pressable className="landing-library-button" onClick={onOpenLibrary}>{NAVIGATION.openLibrary}</Pressable>
            ) : (
              <Pressable className="landing-signin-button" onClick={(event) => requestAuth('login', event.currentTarget)}>{NAVIGATION.signIn}</Pressable>
            )}
          </div>
        </div>
      </header>

      <main className="landing-main">
        <Hero
          signedIn={signedIn}
          onRequestAuth={requestAuth}
          onOpenLibrary={onOpenLibrary}
        />
        <Journey
          scrollContainer={pageRef}
          persona={persona}
          onPersonaChange={setPersona}
          signedIn={signedIn}
          onRequestAuth={requestAuth}
          onOpenLibrary={onOpenLibrary}
        />
        <BuiltBy />
      </main>
      <Footer />
    </div>
  );
}
