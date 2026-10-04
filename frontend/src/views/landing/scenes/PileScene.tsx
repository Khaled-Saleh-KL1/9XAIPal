import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { playful } from '../../../motion/springs';

const scatter = [
  { x: -116, y: 80, rotate: -18 },
  { x: 88, y: 50, rotate: 13 },
  { x: -68, y: -66, rotate: 17 },
  { x: 138, y: -78, rotate: -14 },
  { x: 30, y: 106, rotate: 11 },
  { x: -136, y: -21, rotate: 8 },
  { x: 117, y: 96, rotate: -9 },
];

const slots = [
  { left: '3%', top: '18%' }, { left: '27%', top: '18%' }, { left: '51%', top: '18%' }, { left: '75%', top: '18%' },
  { left: '14%', top: '55%' }, { left: '38%', top: '55%' }, { left: '62%', top: '55%' },
];

function DocumentCard({ index, title, progress }: { index: number; title: string; progress: MotionValue<number> }) {
  const start = scatter[index];
  const gatherAt = 0.52 + index * 0.018;
  const landAt = 0.66 + index * 0.014;
  const x = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.x, start.x * 0.14, -5, 0]), playful);
  const y = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.y, start.y * 0.1, -6, 0]), playful);
  const rotate = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.rotate, start.rotate * 0.12, 3, 0]), playful);
  const reducedMotion = useReducedMotion();
  const isArabic = index === 2;
  const slot = slots[index];

  return (
    <m.article
      className={`pile-document${isArabic ? ' is-arabic' : ''}`}
      style={{ ...slot, ...(reducedMotion ? {} : { x, y, rotate }) }}
      dir={isArabic ? 'rtl' : 'ltr'}
      lang={isArabic ? 'ar' : 'en'}
      aria-label={title}
    >
      <span className="pile-document-type">{SCENE_COPY.pileMedia[index]}</span>
      <span className="pile-document-title">{title}</span>
      <span className="pile-document-lines" aria-hidden="true"><i /><i /><i /></span>
      <span className="pile-document-corner" aria-hidden="true">{isArabic ? 'ع' : index + 1}</span>
    </m.article>
  );
}

export function PileScene({ progress, persona }: { progress: MotionValue<number>; persona: Persona }) {
  const titles = [...PERSONAS[persona].docs, ...SCENE_COPY.pileDocs].slice(0, 7);
  return (
    <div className="pile-scene" aria-label={SCENE_COPY.pileKicker}>
      <div className="scene-paper-wash" aria-hidden="true" />
      <p className="pile-scene-kicker"><span className="scene-marker" />{SCENE_COPY.pileKicker}</p>
      <div className="pile-shelf" aria-hidden="true"><span /><span /></div>
      <AnimatePresence mode="wait" initial={false}>
        <m.div className="pile-cards" key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          {titles.map((title, index) => <DocumentCard key={title} index={index} title={title} progress={progress} />)}
        </m.div>
      </AnimatePresence>
      <div className="pile-library-tab" aria-hidden="true">
        <span />
        <AnimatePresence mode="wait" initial={false}>
          <m.span key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            {persona === 'student' ? SCENE_COPY.pileShelfStudent : SCENE_COPY.pileShelfResearcher}
          </m.span>
        </AnimatePresence>
      </div>
    </div>
  );
}
