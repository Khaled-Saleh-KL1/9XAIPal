import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PaperMeta } from '../api';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  listPapers: vi.fn(),
  deletePaper: vi.fn(),
  renamePaper: vi.fn(),
  setPaperDone: vi.fn(),
  renameDoneFolder: vi.fn(),
  searchPapersSemantic: vi.fn(),
  confirmArabicWritingStyle: vi.fn(),
  getCoverUrl: vi.fn((id: string) => `/cover/${id}`),
  confirm: vi.fn(),
  reducedMotion: false,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return { ...actual, useReducedMotion: () => mocks.reducedMotion };
});

vi.mock('../api', () => ({
  listPapers: mocks.listPapers,
  deletePaper: mocks.deletePaper,
  renamePaper: mocks.renamePaper,
  setPaperDone: mocks.setPaperDone,
  renameDoneFolder: mocks.renameDoneFolder,
  searchPapersSemantic: mocks.searchPapersSemantic,
  confirmArabicWritingStyle: mocks.confirmArabicWritingStyle,
  getCoverUrl: mocks.getCoverUrl,
}));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => mocks.confirm }));
vi.mock('../components/UserMenu', () => ({ UserMenuInline: () => null }));
vi.mock('../components/ExportWizard', () => ({ ExportWizard: () => null }));

import { LibraryView } from './LibraryView';

const meta = (id: string, title: string, status = 'complete'): PaperMeta => ({
  id,
  filename: `${id}.pdf`,
  original_filename: `${id}.pdf`,
  title,
  file_size_bytes: 1000,
  page_count: 4,
  status,
  error_message: null,
  created_at: '2026-01-01T00:00:00.000Z',
  updated_at: null,
  job_status: status === 'complete' ? 'complete' : 'extracting',
});

function renderLibrary(refreshToken = 0) {
  const props = {
    onOpenPaper: vi.fn(),
    onUpload: vi.fn(),
    onOpenRawFiles: vi.fn(),
    onOpenDesk: vi.fn(),
    layout: 'grid' as const,
    setLayout: vi.fn(),
    refreshToken,
  };
  const view = render(<MotionRoot><LibraryView {...props} /></MotionRoot>);
  return {
    ...view,
    rerenderWithRefreshToken: (next: number) => view.rerender(
      <MotionRoot><LibraryView {...props} refreshToken={next} /></MotionRoot>,
    ),
  };
}

async function flushLibraryLoad() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.deletePaper.mockResolvedValue(undefined);
  mocks.renamePaper.mockResolvedValue({});
  mocks.setPaperDone.mockResolvedValue({});
  mocks.renameDoneFolder.mockResolvedValue(undefined);
  mocks.searchPapersSemantic.mockResolvedValue([]);
  mocks.confirmArabicWritingStyle.mockResolvedValue({ message: 'Saved' });
  mocks.confirm.mockResolvedValue(true);
  mocks.reducedMotion = false;
});

afterEach(() => {
  vi.useRealTimers();
  mocks.reducedMotion = false;
});

describe('LibraryView motion', () => {
  it('does not resurrect a deleted card when a refresh returns its old row during exit', async () => {
    const oldRows = [meta('alpha', 'Alpha paper')];
    mocks.listPapers.mockResolvedValue(oldRows);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Delete this paper' }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.deletePaper).toHaveBeenCalledWith('alpha');

    view.rerenderWithRefreshToken(1);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await waitFor(
      () => expect(screen.queryByRole('button', { name: 'Open Alpha paper' })).not.toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByText('Your library is empty. Drop a PDF above to add your first paper.')).toBeInTheDocument();
  });

  it('filters cards by title and restores them when the query is cleared', async () => {
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    const search = screen.getByPlaceholderText(/Search by title/);
    fireEvent.change(search, { target: { value: 'Alpha' } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 220)); });
    expect(screen.getByRole('button', { name: 'Open Alpha paper' })).toBeInTheDocument();
    await waitFor(
      () => expect(screen.queryByRole('button', { name: 'Open Beta book' })).not.toBeInTheDocument(),
      { timeout: 3000 },
    );

    fireEvent.change(search, { target: { value: '' } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 220)); });
    expect(screen.getByRole('button', { name: 'Open Beta book' })).toBeInTheDocument();
  });

  it('marks only ids arriving after the initial load for the drop-in', async () => {
    mocks.listPapers
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper')])
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    view.rerenderWithRefreshToken(1);
    const arriving = await screen.findByRole('button', { name: 'Open Beta book' });
    expect(arriving.closest('[data-testid="paper-motion-item"]')).toHaveAttribute('data-arrival', 'true');
    expect(screen.getByRole('button', { name: 'Open Alpha paper' }).closest('[data-testid="paper-motion-item"]'))
      .not.toHaveAttribute('data-arrival', 'true');
  });

  it('keeps cards transform-free with reduced motion enabled', async () => {
    mocks.reducedMotion = true;
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper')]);
    renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    const item = screen.getByTestId('paper-motion-item');
    expect((item as HTMLElement).style.transform).not.toMatch(/translate|rotate|scale/);
  });

  it('keeps the same card nodes when a processing poll returns fresh objects for the same ids', async () => {
    vi.useFakeTimers();
    mocks.reducedMotion = true;
    mocks.listPapers.mockImplementation(async () => [meta('alpha', 'Alpha paper', 'processing')]);
    renderLibrary();
    await flushLibraryLoad();

    const original = screen.getByRole('button', { name: 'Open Alpha paper' });
    expect(original.closest('[data-testid="paper-motion-item"]')).not.toHaveAttribute('data-arrival', 'true');

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(mocks.listPapers).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Open Alpha paper' }).isSameNode(original)).toBe(true);
  });
});
