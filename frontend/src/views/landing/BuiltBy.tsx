import { m, useReducedMotion } from 'motion/react';
import { AUTHOR, BUILT_BY_TITLE, LANDING_COPY, LINK_LABELS, LINKS } from '../../landing/content';
import { Pressable, Reveal, useTilt } from '../../motion';
import { useMediaQuery } from './useMediaQuery';

export function BuiltBy() {
  const tilt = useTilt(6);
  const reducedMotion = useReducedMotion();
  const smallScreen = useMediaQuery('(max-width: 640px)');
  const tiltEnabled = !smallScreen && !reducedMotion;
  return (
    <section id="built-by" className="landing-builtby" aria-labelledby="built-by-title">
      <Reveal as="h2" id="built-by-title" className="builtby-label"><span className="builtby-rule" />{BUILT_BY_TITLE}</Reveal>
      <div className="builtby-content">
        <Reveal className="builtby-monogram-wrap">
          <m.div
            className="builtby-monogram"
            ref={tilt.ref}
            style={tiltEnabled ? tilt.style : undefined}
            onPointerMove={tiltEnabled ? tilt.onPointerMove : undefined}
            onPointerLeave={tiltEnabled ? tilt.onPointerLeave : undefined}
            aria-label={AUTHOR.name}
          >
            <span>{AUTHOR.monogram}</span>
            <i className="monogram-orbit" aria-hidden="true" />
          </m.div>
        </Reveal>
        <div className="builtby-copy">
          <Reveal as="article">
            <h3 id="built-by-heading">{AUTHOR.name}</h3>
            <p className="builtby-role">{AUTHOR.role}<span className="builtby-role-mark" aria-hidden="true" /></p>
          </Reveal>
          <Reveal delay={0.07}>
            {AUTHOR.bio.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          </Reveal>
          <Reveal delay={0.12} className="builtby-links">
            <Pressable as="a" className="builtby-link" href={LINKS.portfolio} target="_blank" rel="noopener noreferrer">{LINK_LABELS.portfolio}</Pressable>
            <Pressable as="a" className="builtby-link" href={LINKS.repo} target="_blank" rel="noopener noreferrer">{LINK_LABELS.repo}</Pressable>
          </Reveal>
        </div>
        <p className="builtby-aside" aria-hidden="true">{LANDING_COPY.builtByAside[0]}<br />{LANDING_COPY.builtByAside[1]}</p>
      </div>
    </section>
  );
}
