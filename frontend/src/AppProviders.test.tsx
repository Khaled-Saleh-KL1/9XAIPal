import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AppProviders } from './AppProviders';
import { useConfirm } from './components/ConfirmDialog';

function DeleteProbe() {
  const confirm = useConfirm();
  return (
    <button type="button" onClick={() => void confirm({ title: 'Delete this paper?', confirmLabel: 'Delete', tone: 'danger' })}>
      Delete
    </button>
  );
}

describe('AppProviders', () => {
  // The confirm sheet is a motion component. Rendered outside MotionRoot's
  // LazyMotion it never loads its animation features, so it stayed at its
  // initial opacity 0: an invisible dialog with focus already on "Delete".
  it('shows the confirm sheet visibly, not stuck at its initial hidden state', async () => {
    render(
      <AppProviders>
        <DeleteProbe />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete this paper?' });
    await expect.poll(() => getComputedStyle(dialog).opacity, { timeout: 3000 }).toBe('1');
  });
});
