import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { reducedMotionFade } from '../motion/springs';
import { motionPrefs } from '../test/setup';

const { login, signup } = vi.hoisted(() => ({ login: vi.fn(), signup: vi.fn() }));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ login, signup }) }));
vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
});

import { AuthForm } from './AuthForm';

const renderForm = (mode: 'login' | 'signup') => {
  const firstFieldRef = createRef<HTMLInputElement>();
  const result = render(
    <MotionRoot>
      <AuthForm initialMode={mode} titleId="auth-title" firstFieldRef={firstFieldRef} />
    </MotionRoot>,
  );
  return { ...result, firstFieldRef };
};

beforeEach(() => {
  login.mockReset();
  signup.mockReset();
});

describe('AuthForm', () => {
  it('opens in the requested mode and focuses the email field through its ref', () => {
    const { firstFieldRef } = renderForm('signup');
    expect(screen.getByText('Create an account')).toHaveAttribute('id', 'auth-title');
    const displayName = screen.getByPlaceholderText('Display name (optional)');
    expect(displayName).toBeInTheDocument();
    expect(displayName.parentElement?.style.height).toBe('');
    expect(displayName.parentElement?.style.overflow).toBe('');
    expect(firstFieldRef.current).toBe(screen.getByPlaceholderText('Email'));
  });

  it('logs in with the typed credentials', async () => {
    login.mockResolvedValue(undefined);
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'secret123');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(login).toHaveBeenCalledWith('a@b.co', 'secret123');
  });

  it('ignores a second submit while the first is running', async () => {
    let resolve!: () => void;
    login.mockReturnValue(new Promise<void>((r) => { resolve = r; }));
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'secret123');
    const form = screen.getByRole('button', { name: 'Log in' }).closest('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(login).toHaveBeenCalledTimes(1);
    resolve();
  });

  it('shows the server error after a failed login', async () => {
    login.mockRejectedValue(new Error('Invalid email or password'));
    renderForm('login');
    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'wrongpass');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password');
  });

  it('uses short, transform-free reduced-motion transitions for auth changes and errors', async () => {
    motionPrefs.reducedMotion = true;
    signup.mockRejectedValue(new Error('Invalid email or password'));
    const { container } = renderForm('login');

    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));
    const title = await screen.findByRole('heading', { name: 'Create an account' });
    expect(title.style.transform).not.toMatch(/translateY|matrix/);

    await userEvent.type(screen.getByPlaceholderText('Email'), 'a@b.co');
    await userEvent.type(screen.getByPlaceholderText('Password'), 'wrongpass');
    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));
    await screen.findByRole('alert');
    expect(container.querySelector('form')?.style.transform).not.toMatch(/translateX|matrix/);
    expect(reducedMotionFade.duration).toBeLessThanOrEqual(0.15);
  });
});
