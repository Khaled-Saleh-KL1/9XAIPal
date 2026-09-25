import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ArabicOcrStatus } from './ArabicOcrStatus';

describe('ArabicOcrStatus', () => {
  it('keeps handwritten unavailability visible in the library', () => {
    render(
      <ArabicOcrStatus
        errorCode="handwritten_arabic_unavailable"
        errorMessage="Handwritten Arabic extraction is unavailable."
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Handwritten Arabic extraction is unavailable.');
  });

  it('sends the selected writing style', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ArabicOcrStatus
        errorCode="arabic_style_confirmation_required"
        actionRequired="confirm_arabic_writing_style"
        allowedActions={['printed', 'handwritten']}
        onConfirmWritingStyle={onConfirm}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Printed' }));
    expect(onConfirm).toHaveBeenCalledWith('printed');
  });

  it('disables the style choices while the library action is pending', () => {
    render(
      <ArabicOcrStatus
        actionRequired="confirm_arabic_writing_style"
        allowedActions={['printed', 'handwritten']}
        confirmationPending
        onConfirmWritingStyle={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Printed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Handwritten' })).toBeDisabled();
  });
});
