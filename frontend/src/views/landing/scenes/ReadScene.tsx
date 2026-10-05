import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useReducedMotion, useTransform } from 'motion/react';
import { SCENE_COPY, type Persona } from '../../../landing/content';

export function ReadScene({ progress, persona }: { progress: MotionValue<number>; persona: Persona }) {
  const reducedMotion = useReducedMotion();
  const pageScale = useTransform(progress, [0, 0.34, 1], [0.76, 0.92, 1]);
  const contentY = useTransform(progress, [0.12, 0.55, 1], [30, 5, 0]);
  const equationY = useTransform(progress, [0.16, 0.54, 1], [20, 4, 0]);
  const arabicReveal = useTransform(progress, [0.18, 0.72], ['inset(0 100% 0 0)', 'inset(0 0% 0 0)']);
  return (
    <div className="read-scene" aria-label={SCENE_COPY.readFigure}>
      <div className="reader-toolbar"><span /><span /><span /><i /></div>
      <m.article
        className="reader-paper"
        style={reducedMotion ? undefined : { scaleY: pageScale, transformOrigin: 'left center' }}
      >
        <div className="reader-page-heading">
          <AnimatePresence mode="wait" initial={false}>
            <m.span key={persona} className="reader-page-number" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>{persona === 'student' ? SCENE_COPY.readStudentPage : SCENE_COPY.readResearcherPage}</m.span>
          </AnimatePresence>
          <span className="reader-page-rule" />
        </div>
        <div className="reader-paragraph-lines" aria-hidden="true"><i /><i /><i /><i /></div>
        <m.figure className="reader-figure" style={reducedMotion ? undefined : { y: contentY }}>
          <svg viewBox="0 0 230 96" role="img" aria-label={SCENE_COPY.readFigureLabel}>
            <path className="figure-grid" d="M21 12v67h196M21 57h196M21 35h196" />
            <path className="figure-line figure-line-one" d="M24 68 C51 62 54 46 82 50 S118 40 139 34 S178 24 214 20" />
            <path className="figure-line figure-line-two" d="M24 72 C48 70 62 66 82 60 S113 54 139 50 S177 42 214 38" />
            <circle className="figure-point" cx="139" cy="34" r="4" />
          </svg>
          <figcaption>{SCENE_COPY.readFigure}</figcaption>
        </m.figure>
        <m.div className="reader-equation" style={reducedMotion ? undefined : { y: equationY }} role="img" aria-label={SCENE_COPY.readEquationLabel}>
          <span>θ</span><small>t+1</small><b>=</b><span>θ</span><small>t</small><span>− α</span><span className="equation-fraction"><i>∂L</i><i>∂θ</i></span>
        </m.div>
        <m.div className="reader-arabic" style={reducedMotion ? undefined : { clipPath: arabicReveal }} dir="rtl" lang="ar">{SCENE_COPY.readArabic}</m.div>
        <div className="reader-bottom-lines" aria-hidden="true"><i /><i /><i /></div>
      </m.article>
      <div className="reader-page-shadow" aria-hidden="true" />
      <AnimatePresence mode="wait" initial={false}>
        <m.span key={persona} className="reader-side-note" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          {persona === 'student' ? SCENE_COPY.readStudentSideNote : SCENE_COPY.readResearcherSideNote}
        </m.span>
      </AnimatePresence>
    </div>
  );
}
