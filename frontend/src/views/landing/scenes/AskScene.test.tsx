import { render } from '@testing-library/react';
import { motionValue } from 'motion/react';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from '../../../motion';
import { PERSONAS } from '../../../landing/content';
import { AskScene } from './AskScene';

describe('AskScene', () => {
  it('keeps the user avatar distinct and exposes the complete answer without a prohibited paragraph label', () => {
    const { container } = render(
      <MotionRoot><AskScene progress={motionValue(0.55)} persona="student" /></MotionRoot>,
    );

    expect(container.querySelector('.ask-avatar')).toHaveTextContent('You');
    expect(container.querySelector('.ask-answer-mark')).toHaveTextContent('9');
    const answer = container.querySelector('.ask-answer-copy');
    expect(answer).not.toHaveAttribute('aria-label');
    expect(answer?.querySelector('.sr-only')).toHaveTextContent(PERSONAS.student.answer);
    expect(answer?.querySelectorAll('[aria-hidden="true"]')).toHaveLength(PERSONAS.student.answer.split(' ').length);
    expect(container.querySelector('.ask-source-page')).toBeInTheDocument();
    expect(container.querySelector('.ask-source-page')).toHaveClass('is-lit');
    expect(container.querySelectorAll('.citation-chip')).toHaveLength(PERSONAS.student.citations.length);
  });
});
