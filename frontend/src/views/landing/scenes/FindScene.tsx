import { useState } from 'react';
import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useMotionValueEvent, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { calm, playful } from '../../../motion/springs';

function SearchContents({ persona, progress }: { persona: Persona; progress: MotionValue<number> }) {
  const query = PERSONAS[persona].search;
  const documents = [...PERSONAS[persona].docs, SCENE_COPY.findExtraDoc];
  const progressCount = useTransform(progress, [0.08, 0.43], [0, query.length]);
  const [typedCount, setTypedCount] = useState(() => Math.floor(progressCount.get()));
  const reducedMotion = useReducedMotion();
  useMotionValueEvent(progressCount, 'change', (value) => setTypedCount(Math.floor(value)));

  return (
    <m.div
      className="find-search-content"
      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={reducedMotion ? calm : playful}
    >
      <p className="find-scene-label">{SCENE_COPY.findLabel}</p>
      <div className="find-search-box" role="search">
        <span className="find-search-icon" aria-hidden="true">⌕</span>
        <span className="sr-only">{query}</span>
        <span aria-hidden="true">{reducedMotion ? query : query.slice(0, typedCount)}<i className="typing-caret" /></span>
        <kbd>{SCENE_COPY.findShortcut}</kbd>
      </div>
      <p className="find-search-status">{SCENE_COPY.findStatus}</p>
      <ul className="find-results">
        {documents.map((title, index) => {
          const matched = persona === 'student' ? index < 2 : index === 1 || index === 2;
          const pageLabel = persona === 'student'
            ? SCENE_COPY.findStudentPageLabels[index]
            : SCENE_COPY.findResearcherPageLabels[index];
          return <FindResult key={`${persona}-${title}`} title={title} index={index} matched={matched} pageLabel={pageLabel} progress={progress} />;
        })}
      </ul>
    </m.div>
  );
}

function FindResult({ title, index, matched, pageLabel, progress }: { title: string; index: number; matched: boolean; pageLabel: string; progress: MotionValue<number> }) {
  const reducedMotion = useReducedMotion();
  const opacity = useTransform(progress, [0.28, 0.64, 1], [1, matched ? 1 : 0.35, matched ? 1 : 0.35]);
  const y = useSpring(useTransform(progress, [0.28, 0.64, 0.78, 1], [14, 3, -5, 0]), playful);
  return (
    <m.li className={matched ? 'find-result is-match' : 'find-result'} style={reducedMotion ? undefined : { opacity, y }}>
      <span className="find-result-type">{index === 2 ? SCENE_COPY.findArabicLabel : SCENE_COPY.findPdfLabel}</span>
      <span className="find-result-title">{title}</span>
      <span className="find-result-page">{matched ? pageLabel : SCENE_COPY.findNoPage}</span>
      {matched && <i className="find-result-mark" aria-hidden="true" />}
    </m.li>
  );
}

export function FindScene({ progress, persona }: { progress: MotionValue<number>; persona: Persona }) {
  return (
    <div className="find-scene">
      <div className="find-shelf-back" aria-hidden="true"><span /><span /><span /></div>
      <m.div className="find-scene-window" aria-label={SCENE_COPY.findLabel}>
        <AnimatePresence mode="wait" initial={false}>
          <SearchContents key={persona} persona={persona} progress={progress} />
        </AnimatePresence>
      </m.div>
      <div className="find-shelf-edge" aria-hidden="true" />
    </div>
  );
}
