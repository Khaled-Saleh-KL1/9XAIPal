import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { ConfirmProvider, useConfirm } from './ConfirmDialog';
import { ExportWizard } from './ExportWizard';
import { UserMenuInline } from './UserMenu';

const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u', email: 'a@b.co', display_name: 'Khaled' }, logout }),
}));

function ConfirmTrigger({ onResult }: { onResult: (confirmed: boolean) => void }) {
  const confirm = useConfirm();
  return (
    <button onClick={() => { void confirm({ title: 'Delete this paper' }).then(onResult); }}>
      Open confirmation
    </button>
  );
}

describe('shared overlay behavior', () => {
  it('focuses confirm, confirms with Enter once, and returns focus to the opener', async () => {
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <MotionRoot>
        <ConfirmProvider><ConfirmTrigger onResult={onResult} /></ConfirmProvider>
      </MotionRoot>,
    );

    const opener = screen.getByRole('button', { name: 'Open confirmation' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Delete this paper' });
    const confirm = screen.getByRole('button', { name: 'Confirm' });
    expect(confirm).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(dialog).not.toBeInTheDocument(), { timeout: 3000 });
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith(true);
    await vi.waitFor(() => expect(opener).toHaveFocus());
  });

  it('closes only the top confirmation on Escape while the user menu stays open', async () => {
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <MotionRoot>
        <UserMenuInline />
        <ConfirmProvider><ConfirmTrigger onResult={onResult} /></ConfirmProvider>
      </MotionRoot>,
    );

    await user.click(screen.getByRole('button', { name: 'Khaled' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open confirmation' }));
    await user.keyboard('{Escape}');

    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith(false);
  });

  it('traps ExportWizard focus, closes on Escape, and restores the opener', async () => {
    const user = userEvent.setup();
    render(<MotionRoot><ExportWizard papers={[]} /></MotionRoot>);

    const opener = screen.getByRole('button', { name: /Export/ });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Export' });
    const search = screen.getByPlaceholderText('Search by title…');
    expect(search).toHaveFocus();

    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await vi.waitFor(() => expect(opener).toHaveFocus());
  });
});
