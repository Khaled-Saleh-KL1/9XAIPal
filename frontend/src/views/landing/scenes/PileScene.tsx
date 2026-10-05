import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { playful } from '../../../motion/springs';
import { DocumentTitle } from './DocumentTitle';

const scatter = [
  { left: '9%', top: '20%', rotate: -12 },
  { left: '62%', top: '36%', rotate: 8 },
  { left: '39%', top: '16%', rotate: 13 },
  { left: '69%', top: '48%', rotate: -7 },
  { left: '15%', top: '45%', rotate: 10 },
  { left: '48%', top: '29%', rotate: -14 },
  { left: '29%', top: '44%', rotate: 6 },
];

const slots = [
  { left: '0%', rotate: -1.4 },
  { left: '13%', rotate: 0.7 },
  { left: '26%', rotate: -0.9 },
  { left: '39%', rotate: 1.2 },
  { left: '52%', rotate: -1 },
  { left: '65%', rotate: 0.5 },
  { left: '78%', rotate: -0.7 },
];

const depth = [3, 6, 2, 5, 1, 7, 4];

function DocumentCard({ index, title, progress, small }: { index: number; title: string; progress: MotionValue<number>; small: boolean }) {
  const start = scatter[index];
  const slot = slots[index];
  const top = small ? '62%' : '65%';
  const scale = small ? 0.64 : 0.62;
  const landAt = 0.42 + index * 0.028;
  const leftValue = useSpring(useTransform(progress, [0, landAt, 1], [start.left, slot.left, slot.left]), playful);
  const topValue = useSpring(useTransform(progress, [0, landAt, 1], [start.top, top, top]), playful);
  const rotateValue = useSpring(useTransform(progress, [0, landAt, 1], [start.rotate, slot.rotate, slot.rotate]), playful);
  const scaleValue = useSpring(useTransform(progress, [0, landAt, 1], [1, scale, scale]), playful);
  const reducedMotion = useReducedMotion();
  const isArabic = index === 2;

  return (
    <m.article
      className={`pile-document${isArabic ? ' is-arabic' : ''}`}
      style={{
        left: reducedMotion ? slot.left : leftValue,
        top: reducedMotion ? top : topValue,
        zIndex: depth[index],
        rotate: reducedMotion ? slot.rotate : rotateValue,
        scale: reducedMotion ? scale : scaleValue,
      }}
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

export function PileScene({ progress, persona, small = false }: { progress: MotionValue<number>; persona: Persona; small?: boolean }) {
  const titles = [...PERSONAS[persona].docs, ...SCENE_COPY.pileDocs].slice(0, 7);
  return (
    <div className="pile-scene" aria-label={SCENE_COPY.pileKicker}>
      <div className="scene-paper-wash" aria-hidden="true" />
      <p className="pile-scene-kicker"><span className="scene-marker" />{SCENE_COPY.pileKicker}</p>
      <div className="pile-shelf" aria-hidden="true"><span /><span /></div>
      <AnimatePresence mode="wait" initial={false}>
        <m.div className="pile-cards" key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          {titles.map((title, index) => <DocumentCard key={title} index={index} title={title} progress={progress} small={small} />)}
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
