import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { ConfirmProvider, useConfirm } from './ConfirmDialog';
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

beforeEach(() => {
  logout.mockReset();
  window.history.replaceState(null, '', '#/library');
});

describe('Pressable shared chrome adoption', () => {
  it('keeps the user menu buttons named, animated, and working', async () => {
    const user = userEvent.setup();
    render(<MotionRoot><UserMenuInline /></MotionRoot>);

    const opener = screen.getByRole('button', { name: 'Khaled' });
    expect(opener).toHaveClass('motion-pressable');
    await user.click(opener);

    const about = screen.getByRole('menuitem', { name: 'About 9XAIPal' });
    const signOut = screen.getByRole('menuitem', { name: 'Sign out' });
    expect(about).toHaveClass('motion-pressable');
    expect(signOut).toHaveClass('motion-pressable');
    await user.click(about);
    expect(window.location.hash).toBe('#/welcome');
  });

  it('keeps confirmation buttons named, animated, and working', async () => {
    const user = userEvent.setup();
    const onResult = vi.fn();
    render(
      <MotionRoot>
        <ConfirmProvider><ConfirmTrigger onResult={onResult} /></ConfirmProvider>
      </MotionRoot>,
    );

    await user.click(screen.getByRole('button', { name: 'Open confirmation' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete this paper' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const confirm = screen.getByRole('button', { name: 'Confirm' });
    expect(cancel).toHaveClass('motion-pressable');
    expect(confirm).toHaveClass('motion-pressable');
    await user.click(confirm);
    expect(dialog).not.toBeInTheDocument();
    expect(onResult).toHaveBeenCalledWith(true);
  });
});
