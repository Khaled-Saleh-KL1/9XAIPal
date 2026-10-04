import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { playful } from '../../../motion/springs';

function CollectNote({ persona, progress }: { persona: Persona; progress: MotionValue<number> }) {
  const reducedMotion = useReducedMotion();
  const x = useSpring(useTransform(progress, [0, 0.42, 0.76, 1], [150, 92, 16, 0]), playful);
  const y = useSpring(useTransform(progress, [0, 0.42, 0.76, 1], [-115, -35, 9, 0]), playful);
  const rotateX = useTransform(progress, [0, 0.35, 0.7, 1], [58, 22, 3, 0]);
  const rotate = useSpring(useTransform(progress, [0, 0.42, 0.76, 1], [-13, -9, 4, 2]), playful);
  return (
    <m.article className="collect-note collect-note-main" style={reducedMotion ? undefined : { x, y, rotateX, rotate }}>
      <span className="collect-note-pin" aria-hidden="true" />
      <span className="collect-note-label">{SCENE_COPY.collectKicker}</span>
      <p>{PERSONAS[persona].note}</p>
      <span className="collect-note-foot">{PERSONAS[persona].citations[0]}</span>
    </m.article>
  );
}

export function CollectScene({ progress, persona }: { progress: MotionValue<number>; persona: Persona }) {
  const reducedMotion = useReducedMotion();
  const groupScale = useTransform(progress, [0.68, 0.92, 1], [0.94, 1.03, 1]);
  return (
    <div className="collect-scene" aria-label={SCENE_COPY.collectKicker}>
      <div className="desk-lamp-light" aria-hidden="true" />
      <div className="desk-surface">
        <div className="desk-pencil" aria-hidden="true"><span /></div>
        <m.div className="collect-note-stack" style={reducedMotion ? undefined : { scale: groupScale }}>
          <article className="collect-note collect-note-back"><span className="collect-note-pin" aria-hidden="true" /><p>{SCENE_COPY.collectOtherNotes[0]}</p><span className="collect-note-foot"><AnimatePresence mode="wait" initial={false}><m.span key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>{PERSONAS[persona].citations[1]}</m.span></AnimatePresence></span></article>
          <article className="collect-note collect-note-side"><span className="collect-note-pin" aria-hidden="true" /><p>{SCENE_COPY.collectPersonaNote[persona]}</p><span className="collect-note-foot"><AnimatePresence mode="wait" initial={false}><m.span key={persona} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>{PERSONAS[persona].citations[0]}</m.span></AnimatePresence></span></article>
          <AnimatePresence mode="wait" initial={false}>
            <CollectNote key={persona} persona={persona} progress={progress} />
          </AnimatePresence>
        </m.div>
        <span className="desk-edge" aria-hidden="true" />
      </div>
    </div>
  );
}
