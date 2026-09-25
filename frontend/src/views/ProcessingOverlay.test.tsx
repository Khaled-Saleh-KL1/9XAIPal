import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProcessingOverlay } from './ProcessingOverlay';

const file = { name: 'arabic.pdf', size: '1.2 MB', pages: 0 };
const noop = () => {};

describe('ProcessingOverlay Arabic states', () => {
  it('keeps the full handwritten-unavailable message in a blocking alert', () => {
    const message =
      'Handwritten Arabic extraction is unavailable. This deployment does not have a billing-enabled account with Gemini Pro access; printed Arabic documents can still be processed.';

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
