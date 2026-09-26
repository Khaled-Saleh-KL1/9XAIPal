import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProcessingOverlay } from './ProcessingOverlay';

const file = { name: 'arabic.pdf', size: '1.2 MB', pages: 0 };
const noop = () => {};

describe('ProcessingOverlay Arabic states', () => {
  it('shows local classifier failure as an actionable alert', () => {
    render(
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

    render(
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
    render(
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
    render(
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
    render(
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
