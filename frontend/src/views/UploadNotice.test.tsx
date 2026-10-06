import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { UploadNotice } from './UploadNotice';

afterEach(() => { cleanup(); vi.useRealTimers(); });
it.each([
  ['queue_full', 'The server is busy processing other uploads.'],
  ['user_queue_full', 'Your earlier uploads are still processing.'],
  ['storage_full', 'The server needs more storage space before accepting uploads.'],
  ['service_unavailable', 'Uploads are temporarily unavailable.'],
] as const)('shows %s with a server countdown and retry', (code,message) => {
  vi.useFakeTimers();
  const retry = vi.fn();
  render(<UploadNotice code={code} retryAfter={30} onRetry={retry} />);
  expect(screen.getByText(message)).toBeInTheDocument();
  expect(screen.getByText('Try again in 30 seconds.')).toBeInTheDocument();
  expect(screen.getByRole('button', {name:'Try again'})).toBeDisabled();
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByText('Try again in 29 seconds.')).toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(29000); });
  expect(retry).toHaveBeenCalledTimes(1);
});
it('shows the duplicate notice', () => {
  render(<UploadNotice duplicate />);
  expect(screen.getByText('This file is already in your library')).toBeInTheDocument();
});
it('offers to open the existing document', () => {
  const open = vi.fn();
  render(<UploadNotice duplicate onOpen={open} />);
  screen.getByRole('button', {name:'Open document'}).click();
  expect(open).toHaveBeenCalledTimes(1);
});
