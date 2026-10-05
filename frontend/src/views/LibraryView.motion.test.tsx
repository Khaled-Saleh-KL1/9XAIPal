import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  paperMotionProps: new Map<string, Record<string, unknown>>(),
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableMotionDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    if (props['data-testid'] === 'paper-motion-item') {
      const paperChild = React.Children.toArray(props.children)
        .find((child) => React.isValidElement(child) && (child.props as any).paper);
      const paperId = React.isValidElement(paperChild) ? (paperChild.props as any).paper?.id : undefined;
      if (paperId) mocks.paperMotionProps.set(paperId, props);
    }
    if (props['data-testid'] === 'paper-arrival-glow') {
      mocks.paperMotionProps.set(`arrival-glow:${props['data-paper-id']}`, props);
    }
    if (props['data-testid'] === 'library-motion-layout') mocks.paperMotionProps.set('layout', props);
    return React.createElement(actual.m.div, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      return key === 'div' ? InspectableMotionDiv : Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m: inspectedM, useReducedMotion: () => mocks.reducedMotion };
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
    props,
    rerenderWithRefreshToken: (next: number) => view.rerender(
      <MotionRoot><LibraryView {...props} refreshToken={next} /></MotionRoot>,
    ),
    rerenderWithLayout: (next: 'grid' | 'list') => view.rerender(
      <MotionRoot><LibraryView {...props} layout={next} /></MotionRoot>,
    ),
  };
}

async function flushLibraryLoad() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.paperMotionProps.clear();
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

  it('keeps an arriving card target through a poll after its entrance starts', async () => {
    mocks.listPapers
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper')])
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')])
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    view.rerenderWithRefreshToken(1);
    await screen.findByRole('button', { name: 'Open Beta book' });
    const entrance = mocks.paperMotionProps.get('beta')!;
    expect(entrance.animate).toMatchObject({ opacity: 1, y: 0, rotate: 0, scale: 1 });

    act(() => { (entrance.onAnimationStart as (() => void) | undefined)?.(); });
    view.rerenderWithRefreshToken(2);
    await flushLibraryLoad();

    expect(mocks.paperMotionProps.get('beta')?.animate).toEqual(entrance.animate);
  });

  it('fades a static accent halo through opacity over 800ms', async () => {
    mocks.listPapers
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper')])
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });
    view.rerenderWithRefreshToken(1);
    await screen.findByRole('button', { name: 'Open Beta book' });

    const glow = mocks.paperMotionProps.get('arrival-glow:beta')!;
    expect(glow).toBeDefined();
    expect(glow.style).toMatchObject({ boxShadow: '0 0 0 3px var(--accent)' });
    expect(glow.initial).toMatchObject({ opacity: expect.any(Number) });
    expect(glow.animate).toEqual({ opacity: 0 });
    expect((glow.transition as { opacity: { duration: number } }).opacity.duration).toBe(0.8);

    const glowNode = screen.getByTestId('paper-arrival-glow');
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 400)); });
    const intermediateOpacity = Number((glowNode as HTMLElement).style.opacity);
    expect(intermediateOpacity).toBeGreaterThan(0);
    expect(intermediateOpacity).toBeLessThan(Number((glow.initial as { opacity: number }).opacity));
  });

  it('keeps cards transform-free with reduced motion enabled', async () => {
    mocks.reducedMotion = true;
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper')]);
    renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });

    const item = screen.getByRole('button', { name: 'Open Alpha paper' })
      .closest('[data-testid="paper-motion-item"]') as HTMLElement;
    expect((item as HTMLElement).style.transform).not.toMatch(/translate|rotate|scale/);
  });

  it('caps library entrance and exit fades at 150ms with reduced motion', async () => {
    mocks.reducedMotion = true;
    mocks.listPapers
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper')])
      .mockResolvedValueOnce([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });
    view.rerenderWithRefreshToken(1);
    await screen.findByRole('button', { name: 'Open Beta book' });

    const layout = mocks.paperMotionProps.get('layout')!;
    const card = mocks.paperMotionProps.get('alpha')!;
    const arrivalGlow = mocks.paperMotionProps.get('arrival-glow:beta')!;
    const exit = (card.variants as { exit: (custom: Set<string>) => { transition: { duration: number } } })
      .exit(new Set());
    expect((layout.transition as { duration: number }).duration).toBeLessThanOrEqual(0.15);
    expect((card.transition as { duration: number }).duration).toBeLessThanOrEqual(0.15);
    expect(exit.transition.duration).toBeLessThanOrEqual(0.15);
    expect((arrivalGlow.transition as { opacity: { duration: number } }).opacity.duration).toBeLessThanOrEqual(0.15);
  });

  it('keeps the mobile upload trigger keyboard reachable and passes it as the focus-return target', async () => {
    mocks.listPapers.mockResolvedValue([]);
    const view = renderLibrary();
    await screen.findByText('Your library is empty. Drop a PDF above to add your first paper.');

    const upload = screen.getByRole('button', { name: 'Add paper' });
    expect(upload.closest('.hidden')).toBeNull();
    upload.focus();
    await userEvent.keyboard('{Enter}');

    expect(view.props.onUpload).toHaveBeenCalledWith(undefined, upload);
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

  it('makes an exiting card inert before its exit animation finishes', async () => {
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper'), meta('beta', 'Beta book')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Beta book' });

    const exitingCard = screen.getByRole('button', { name: 'Open Beta book' })
      .closest('[data-testid="paper-motion-item"]') as HTMLElement;
    await userEvent.click(within(exitingCard).getByRole('button', { name: 'Delete this paper' }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(exitingCard).toHaveAttribute('aria-hidden', 'true');
    expect(exitingCard).toHaveAttribute('inert');
    expect(exitingCard).toHaveStyle({ pointerEvents: 'none' });
    view.unmount();
  });

  it('makes the outgoing layout and its cards inert while switching grid and list', async () => {
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper')]);
    const view = renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });
    const outgoingLayout = screen.getByTestId('library-motion-layout');

    view.rerenderWithLayout('list');

    expect(outgoingLayout).toHaveAttribute('aria-hidden', 'true');
    expect(outgoingLayout).toHaveAttribute('inert');
    expect(outgoingLayout).toHaveStyle({ pointerEvents: 'none' });
    expect(within(outgoingLayout).queryByRole('button', { name: 'Open Alpha paper' })).toBeNull();
  });

  it('keeps the shelf sheet mounted during its close animation', async () => {
    mocks.listPapers.mockResolvedValue([meta('alpha', 'Alpha paper')]);
    renderLibrary();
    await screen.findByRole('button', { name: 'Open Alpha paper' });
    await userEvent.click(screen.getByRole('button', { name: 'Mark as done reading' }));
    const dialog = await screen.findByRole('dialog', { name: 'Done reading' });

    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(dialog).toBeInTheDocument();
  });
});
