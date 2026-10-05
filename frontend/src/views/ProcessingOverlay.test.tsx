import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { MotionRoot } from '../motion';
import { ProcessingOverlay } from './ProcessingOverlay';

const file = { name: 'arabic.pdf', size: '1.2 MB', pages: 0 };
const noop = () => {};
const motionPreference = vi.hoisted(() => ({ reduced: false, motionProps: new Map<string, Record<string, any>>() }));
vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableMotionDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const className = typeof props.className === 'string' ? props.className : '';
    if (className.includes('w-[42%]')) motionPreference.motionProps.set('document-sweep', props);
    if (className.includes('right-[-3px]')) motionPreference.motionProps.set('progress-wobble', props);
    if (props['data-testid'] === 'processing-progress-fill') motionPreference.motionProps.set('progress-fill', props);
    return React.createElement(actual.m.div, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      return key === 'div' ? InspectableMotionDiv : Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m: inspectedM, useReducedMotion: () => motionPreference.reduced };
});
const renderOverlay = (children: ReactNode) => render(<MotionRoot>{children}</MotionRoot>);

afterEach(() => { motionPreference.reduced = false; motionPreference.motionProps.clear(); });

describe('ProcessingOverlay extraction route copy', () => {
  it.each([
    ['mineru', 'MinerU is parsing layout, math, and figures'],
    ['gemini_arabic_flash', 'Arabic OCR (Gemini Flash) is reading the pages'],
    ['gemini_arabic_pro', 'Arabic OCR (Gemini Pro) is reading the handwriting'],
    [null, 'Reading layout, text, math, and figures — the pipeline is chosen automatically'],
    ['pymupdf_fallback', 'Reading layout, text, math, and figures — the pipeline is chosen automatically'],
    ['gemma4_arabic_fallback', 'Reading layout, text, math, and figures — the pipeline is chosen automatically'],
  ])('describes the known extractor %s without claiming another route', (extractor, text) => {
    renderOverlay(
      <ProcessingOverlay file={file} status="extracting" extractor={extractor} onClose={noop} onCancel={noop} />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    if (extractor !== 'mineru') {
      expect(screen.queryByText('MinerU is parsing layout, math, and figures')).not.toBeInTheDocument();
    }
  });

  it('updates the neutral copy when the progress poll identifies the route', () => {
    const { rerender } = renderOverlay(
      <ProcessingOverlay file={file} status="extracting" onClose={noop} onCancel={noop} />,
    );
    expect(screen.getByText('Reading layout, text, math, and figures — the pipeline is chosen automatically')).toBeInTheDocument();
    rerender(
      <MotionRoot><ProcessingOverlay file={file} status="extracting" extractor="gemini_arabic_pro" onClose={noop} onCancel={noop} /></MotionRoot>,
    );
    expect(screen.getByText('Arabic OCR (Gemini Pro) is reading the handwriting')).toBeInTheDocument();
    expect(screen.queryByText('Reading layout, text, math, and figures — the pipeline is chosen automatically')).not.toBeInTheDocument();
  });

  it('removes the active step label immediately when processing completes', () => {
    const { rerender } = renderOverlay(
      <ProcessingOverlay file={file} status="extracting" onClose={noop} onCancel={noop} />,
    );
    expect(screen.getByText('running…')).toBeInTheDocument();

    rerender(
      <MotionRoot><ProcessingOverlay file={file} status="complete" onClose={noop} onCancel={noop} /></MotionRoot>,
    );
    expect(screen.queryByText('running…')).not.toBeInTheDocument();
  });

  it('keeps the overlay and progress fill transform-free for reduced motion', () => {
    motionPreference.reduced = true;
    renderOverlay(
      <ProcessingOverlay file={file} status="extracting" progressFraction={0.5} onClose={noop} onCancel={noop} />,
    );

    for (const testId of ['processing-overlay', 'processing-card', 'processing-progress-fill']) {
      expect(screen.getByTestId(testId).style.transform).not.toMatch(/translate|rotate|scale/);
    }
  });

  it('uses reduced-motion fades of at most 150ms for the document sweep and progress indicator', () => {
    motionPreference.reduced = true;
    renderOverlay(
      <ProcessingOverlay file={file} status="extracting" progressFraction={0.5} onClose={noop} onCancel={noop} />,
    );

    const sweep = motionPreference.motionProps.get('document-sweep')!;
    const wobble = motionPreference.motionProps.get('progress-wobble')!;
    const fill = motionPreference.motionProps.get('progress-fill')!;
    expect(sweep.animate).toEqual({ opacity: 0 });
    expect(sweep.transition.duration).toBeLessThanOrEqual(0.15);
    expect(wobble.animate).toEqual({ opacity: 0 });
    expect(wobble.transition.duration).toBeLessThanOrEqual(0.15);
    expect(fill.transition.duration).toBeLessThanOrEqual(0.15);
  });

  it.each([
    ['article', null],
    ['paper', 'trafilatura'],
  ] as const)('preserves article steps for kind %s and extractor %s', (kind, extractor) => {
    renderOverlay(
      <ProcessingOverlay file={file} status="extracting" kind={kind} extractor={extractor} onClose={noop} onCancel={noop} />,
    );
    expect(screen.getByText('Fetching the page')).toBeInTheDocument();
    expect(screen.getByText('Reading the article and pulling out its real images')).toBeInTheDocument();
    expect(screen.getByText('Splitting the article into structural units')).toBeInTheDocument();
    expect(screen.queryByText('Extracting structure')).not.toBeInTheDocument();
  });
});

describe('ProcessingOverlay Arabic states', () => {
  it('shows local classifier failure as an actionable alert', () => {
    renderOverlay(
      <ProcessingOverlay
        file={file}
        status="failed"
        errorCode="arabic_classifier_unavailable"
        onClose={noop}
        onCancel={noop}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Start Ollama');
  });
  it('keeps the full handwritten-unavailable message in a blocking alert', () => {
    const message =
      'Handwritten Arabic extraction is not currently available because it requires Gemini Pro with a billing-enabled account. No text was extracted, and your original file has been kept.';

    renderOverlay(
      <ProcessingOverlay
        file={file}
        status="failed"
        errorCode="handwritten_arabic_unavailable"
        errorMessage={message}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(message);
  });

  it('also blocks the Pro-not-configured state', () => {
    renderOverlay(
      <ProcessingOverlay
        file={file}
        status="failed"
        errorCode="arabic_gemini_pro_not_configured"
        onClose={noop}
        onCancel={noop}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('No text was extracted');
  });

  it('offers printed and handwritten actions and sends the selected value', async () => {
    const user = userEvent.setup();
    const onConfirmWritingStyle = vi.fn();
    renderOverlay(
      <ProcessingOverlay
        file={file}
        status="failed"
        errorCode="arabic_style_confirmation_required"
        actionRequired="confirm_arabic_writing_style"
        allowedActions={['printed', 'handwritten']}
        errorMessage="The Arabic writing style could not be identified confidently."
        onConfirmWritingStyle={onConfirmWritingStyle}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByRole('button', { name: 'Printed' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Handwritten' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Printed' }));
    expect(onConfirmWritingStyle).toHaveBeenCalledWith('printed');
  });

  it('disables both confirmation actions while the choice is saving', () => {
    renderOverlay(
      <ProcessingOverlay
        file={file}
        status="failed"
        errorCode="arabic_style_confirmation_required"
        actionRequired="confirm_arabic_writing_style"
        allowedActions={['printed', 'handwritten']}
        confirmationPending
        onConfirmWritingStyle={noop}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByRole('button', { name: 'Printed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Handwritten' })).toBeDisabled();
  });
});
