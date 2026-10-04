import { useState } from 'react';
import type { MotionValue } from 'motion/react';
import { AnimatePresence, m, useMotionValueEvent, useReducedMotion, useTransform } from 'motion/react';
import { PERSONAS, SCENE_COPY, type Persona } from '../../../landing/content';
import { Pressable } from '../../../motion';
import { calm, playful } from '../../../motion/springs';

function AskContent({ persona, progress }: { persona: Persona; progress: MotionValue<number> }) {
  const data = PERSONAS[persona];
  const words = data.answer.split(' ');
  const questionCountValue = useTransform(progress, [0.03, 0.18], [0, data.question.length]);
  const answerCountValue = useTransform(progress, [0.18, 0.4], [0, words.length]);
  const revealCitations = useTransform(progress, [0.36, 0.55], [0, data.citations.length]);
  const readingHighlight = useTransform(progress, [0.42, 0.6], [0, 1]);
  const [questionCount, setQuestionCount] = useState(() => Math.floor(questionCountValue.get()));
  const [answerCount, setAnswerCount] = useState(() => Math.floor(answerCountValue.get()));
  const [citationCount, setCitationCount] = useState(() => Math.floor(revealCitations.get()));
  const [highlighted, setHighlighted] = useState(() => readingHighlight.get() > 0.65);
  const reducedMotion = useReducedMotion();

  useMotionValueEvent(questionCountValue, 'change', (value) => setQuestionCount(Math.floor(value)));
  useMotionValueEvent(answerCountValue, 'change', (value) => setAnswerCount(Math.floor(value)));
  useMotionValueEvent(revealCitations, 'change', (value) => setCitationCount(Math.floor(value)));
  useMotionValueEvent(readingHighlight, 'change', (value) => setHighlighted(value > 0.65));

  const questionVisible = reducedMotion ? data.question : data.question.slice(0, questionCount);
  const answerVisible = reducedMotion ? words.length : answerCount;
  const visibleCitations = reducedMotion ? data.citations.length : citationCount;

  return (
    <m.div
      className="ask-content"
      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={reducedMotion ? calm : playful}
    >
      <div className="ask-window-bar"><span /><span /><span /><b>{SCENE_COPY.askLabel}</b></div>
      <div className="ask-question-card">
        <span className="ask-avatar" aria-hidden="true">You</span>
        <div className="ask-question-text">
          <span className="sr-only">{data.question}</span>
          <span aria-hidden="true">{questionVisible}<i className="typing-caret" /></span>
        </div>
      </div>
      <div className="ask-answer-row">
        <span className="ask-answer-mark" aria-hidden="true">9</span>
        <div className="ask-answer-card">
          <p className="ask-answer-label">{SCENE_COPY.answerLabel}</p>
          <p className="ask-answer-copy">
            <span className="sr-only">{data.answer}</span>
            {words.slice(0, answerVisible).map((word, index) => (
              <m.span
                key={`${persona}-${index}`}
                initial={reducedMotion ? false : { opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                transition={calm}
                aria-hidden="true"
              >{word}{index < answerVisible - 1 ? ' ' : ''}</m.span>
            ))}
          </p>
          <div className="ask-citations" aria-label={SCENE_COPY.sourcesLabel}>
            {data.citations.slice(0, visibleCitations).map((citation, index) => (
              <Pressable
                key={`${persona}-${citation}`}
                className={`citation-chip${highlighted || index === 0 ? ' is-highlighted' : ''}`}
                onMouseEnter={() => setHighlighted(true)}
                onMouseLeave={() => setHighlighted(false)}
                onFocus={() => setHighlighted(true)}
                onBlur={() => setHighlighted(false)}
              >
                <span className="citation-pin" aria-hidden="true">{index + 1}</span>{citation}
              </Pressable>
            ))}
          </div>
        </div>
      </div>
      <article className={`ask-source-page${highlighted ? ' is-lit' : ''}`}>
        <div className="ask-source-heading"><span>{SCENE_COPY.sourceLabel}</span><i /></div>
        <div className="source-lines" aria-hidden="true"><i /><i /><i className={highlighted ? 'is-lit' : ''} /><i /><i /><i /></div>
        <span className="source-page-number">{data.sourcePage}</span>
      </article>
    </m.div>
  );
}

export function AskScene({ progress, persona }: { progress: MotionValue<number>; persona: Persona }) {
  return (
    <div className="ask-scene" aria-label={SCENE_COPY.askLabel}>
      <div className="ask-surface" aria-hidden="true" />
      <AnimatePresence mode="wait" initial={false}>
        <AskContent key={persona} persona={persona} progress={progress} />
      </AnimatePresence>
    </div>
  );
}
