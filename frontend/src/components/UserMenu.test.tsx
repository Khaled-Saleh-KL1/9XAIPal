import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u', email: 'a@b.co', display_name: 'Khaled' }, logout }),
}));

import { UserMenuInline } from './UserMenu';

beforeEach(() => {
  logout.mockReset();
  window.history.replaceState(null, '', '#/library');
});

it('opens the About page from the user menu', async () => {
  render(<UserMenuInline />);
  await userEvent.click(screen.getByRole('button', { name: 'Khaled' }));
  await userEvent.click(screen.getByRole('menuitem', { name: 'About 9XAIPal' }));
  expect(window.location.hash).toBe('#/welcome');
  expect(screen.queryByRole('menu')).toBeNull();
});
