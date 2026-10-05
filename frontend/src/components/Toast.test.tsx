import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { Toast } from './Toast';

describe('Toast', () => {
  afterEach(() => vi.useRealTimers());

  it('shows the message and dismisses when × is pressed', () => {
    const onDismiss = vi.fn();
    render(
      <MotionRoot>
        <Toast notice={{ text: 'Saved to the server.', tone: 'info' }} onDismiss={onDismiss} />
      </MotionRoot>,
    );

    expect(screen.getByText('Saved to the server.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it.each([
    ['info', 7000],
    ['error', 9000],
  ] as const)('auto-dismisses %s notices after %i ms', async (tone, duration) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const onDismiss = vi.fn();
    render(
      <MotionRoot>
        <Toast notice={{ text: 'A notice.', tone }} onDismiss={onDismiss} />
      </MotionRoot>,
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(duration - 1); });
    expect(onDismiss).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
