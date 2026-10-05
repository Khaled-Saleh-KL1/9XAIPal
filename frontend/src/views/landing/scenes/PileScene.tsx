import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { playful } from '../../../motion/springs';
import { DocumentTitle } from './DocumentTitle';

const scatter = [
  { x: -28, y: -38, rotate: -13 },
  { x: 12, y: -53, rotate: 8 },
  { x: 34, y: -15, rotate: 12 },
  { x: -14, y: -62, rotate: -8 },
  { x: 24, y: -28, rotate: 9 },
  { x: -35, y: -49, rotate: -11 },
  { x: 5, y: -69, rotate: 6 },
];

const slots = [
  { left: '18%', top: '58%' }, { left: '26%', top: '56%' }, { left: '34%', top: '60%' }, { left: '42%', top: '57%' },
  { left: '50%', top: '59%' }, { left: '30%', top: '55%' }, { left: '38%', top: '61%' },
];

const depth = [3, 6, 2, 5, 1, 7, 4];

function DocumentCard({ index, title, progress }: { index: number; title: string; progress: MotionValue<number> }) {
  const start = scatter[index];
  const gatherAt = 0.52 + index * 0.018;
  const landAt = 0.66 + index * 0.014;
  const x = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.x, start.x * 0.14, 0, 0]), playful);
  const y = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.y, start.y * 0.1, 0, 0]), playful);
  const rotate = useSpring(useTransform(progress, [0, gatherAt, landAt, 1], [start.rotate, start.rotate * 0.12, 0, 0]), playful);
  const reducedMotion = useReducedMotion();
  const isArabic = index === 2;
  const slot = slots[index];

  return (
    <m.article
      className={`pile-document${isArabic ? ' is-arabic' : ''}`}
      style={{ ...slot, zIndex: depth[index], ...(reducedMotion ? { x: start.x, y: start.y, rotate: start.rotate } : { x, y, rotate }) }}
      dir={isArabic ? 'rtl' : 'ltr'}
      lang={isArabic ? 'ar' : 'en'}
      aria-label={title}
    >
      <span className="pile-document-type">{SCENE_COPY.pileMedia[index]}</span>
      <span className="pile-document-title"><DocumentTitle title={title} /></span>
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
        <span className="pile-library-icon" />
        <AnimatePresence mode="wait" initial={false}>
          <m.span key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            {persona === 'student' ? SCENE_COPY.pileShelfStudent : SCENE_COPY.pileShelfResearcher}
          </m.span>
        </AnimatePresence>
      </div>
    </div>
  );
}
