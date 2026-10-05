import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { MotionRoot } from './motion';
import { getPaper, uploadPaper } from './api';

const authState = vi.hoisted(() => ({
  user: null as any,
  loading: false,
  admitted: true,
  queuePosition: null as number | null,
  login: vi.fn(),
  signup: vi.fn(),
  logout: vi.fn(),
  refreshAdmission: vi.fn(),
  libraryMounts: 0,
}));

vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => authState,
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock('./views/LibraryView', () => ({
  LibraryView: ({ onUpload, onOpenDesk }: any) => {
    useEffect(() => { authState.libraryMounts += 1; }, []);
    return (
      <div>
        <span>LIBRARY</span>
        <button onClick={(event) => onUpload(undefined, event.currentTarget)}>Add paper</button>
        <button onClick={() => onOpenDesk()}>Open desk</button>
        <button onClick={(event) => onUpload(new File(['sample'], 'sample.pdf', { type: 'application/pdf' }), event.currentTarget)}>Start processing</button>
      </div>
    );
  },
}));
vi.mock('./views/ReadingView', () => ({ ReadingView: () => <div>READING</div> }));
vi.mock('./views/ProcessingOverlay', () => ({ ProcessingOverlay: ({ onClose }: any) => (
  <div>PROCESSING<button onClick={onClose}>Close processing</button></div>
) }));
vi.mock('./views/RawFilesPanel', () => ({ RawFilesPanel: () => null }));
vi.mock('./views/DeskView', () => ({ DeskView: () => <div>DESK</div> }));
vi.mock('./views/RawArticleViewer', () => ({ RawArticleViewer: () => <div>RAW ARTICLE</div> }));
vi.mock('./api', () => ({
  uploadPaper: vi.fn(),
  importArticleUrl: vi.fn(),
  getPaperProgress: vi.fn(),
  listPapers: vi.fn().mockResolvedValue([]),
  getPaper: vi.fn().mockRejectedValue(new Error('missing paper')),
  deletePaper: vi.fn(),
  pageToSequence: vi.fn(),
  confirmArabicWritingStyle: vi.fn(),
  QueueFullError: class QueueFullError extends Error {
    queued = 0;
    limit = 0;
  },
}));

import { App } from './App';

const renderApp = () => render(<MotionRoot><App /></MotionRoot>);

beforeEach(() => {
  authState.user = null;
  authState.loading = false;
  authState.admitted = true;
  authState.queuePosition = null;
  authState.refreshAdmission.mockReset().mockResolvedValue(undefined);
  authState.libraryMounts = 0;
  vi.mocked(uploadPaper).mockResolvedValue({ id: 'upload-id' } as never);
  window.history.replaceState(null, '', '#/library');
});

afterEach(() => vi.useRealTimers());

describe('App gate', () => {
  it('shows the landing page, not the auth form, to a signed-out visitor', () => {
    renderApp();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Read deeper.');
    expect(screen.queryByPlaceholderText('Email')).toBeNull();
  });

  it('opens the sheet in login mode from Sign in and closes it with Escape', async () => {
    renderApp();
    await userEvent.click(screen.getByRole('button', { name: /^sign in$/i }));
    expect(await screen.findByRole('dialog', { name: 'Welcome back' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens the sheet in signup mode from the primary CTA', async () => {
    renderApp();
    await userEvent.click(screen.getAllByRole('button', { name: /try the free beta/i })[0]);
    expect(await screen.findByRole('dialog', { name: 'Create an account' })).toBeInTheDocument();
  });

  it('keeps a deep link while signed out and while the auth sheet is open', async () => {
    window.history.replaceState(null, '', '#/paper/abc');
    renderApp();
    expect(window.location.hash).toBe('#/paper/abc');
    await userEvent.click(screen.getByRole('button', { name: /^sign in$/i }));
    expect(await screen.findByRole('dialog', { name: 'Welcome back' })).toBeInTheDocument();
    expect(window.location.hash).toBe('#/paper/abc');
  });

  it('preserves a signed-out deep link through sign-in and opens its destination', async () => {
    vi.mocked(getPaper).mockResolvedValue({
      id: 'abc',
      filename: 'paper.pdf',
      original_filename: 'paper.pdf',
      file_size_bytes: null,
      page_count: 3,
      status: 'complete',
      error_message: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: null,
    } as never);
    window.history.replaceState(null, '', '#/paper/abc');
    const view = renderApp();
    expect(getPaper).not.toHaveBeenCalled();
    authState.user = { id: 'u', email: 'a@b.co' };
    view.rerender(<MotionRoot><App /></MotionRoot>);
    expect(await screen.findByText('READING')).toBeInTheDocument();
    expect(window.location.hash).toBe('#/paper/abc');
  });

  it('sends a signed-in user straight to the library', () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();
    expect(screen.getByText('LIBRARY')).toBeInTheDocument();
  });

  it('replaces the library with the desk synchronously on route change', () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();
    expect(screen.getByText('LIBRARY')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open desk' }));
    expect(screen.queryByText('LIBRARY')).not.toBeInTheDocument();
    expect(screen.getByText('DESK')).toBeInTheDocument();
  });

  it('keeps LibraryView mounted while the processing overlay opens', async () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();
    const mounts = authState.libraryMounts;

    fireEvent.click(screen.getByRole('button', { name: 'Start processing' }));
    const dialog = await screen.findByRole('dialog', { name: 'What are you adding?' });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Book/ }));

    expect(screen.getByText('LIBRARY')).toBeInTheDocument();
    expect(screen.getByText('PROCESSING')).toBeInTheDocument();
    expect(authState.libraryMounts).toBe(mounts);
    fireEvent.click(screen.getByRole('button', { name: 'Close processing' }));
  });

  it('traps focus in the upload sheet, closes on Escape, and returns focus to its opener', async () => {
    const user = userEvent.setup();
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();

    const opener = screen.getByRole('button', { name: 'Add paper' });
    await user.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'What are you adding?' });
    expect(screen.getByRole('button', { name: /^Book/ })).toHaveFocus();

    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await vi.waitFor(() => expect(opener).toHaveFocus());
  });

  it('shows the landing page at #/welcome to a signed-in user and closes into the library', async () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    window.history.replaceState(null, '', '#/welcome');
    renderApp();
    expect(await screen.findAllByRole('button', { name: /open your library/i })).not.toHaveLength(0);
    expect(window.location.hash).toBe('#/welcome');
    await userEvent.click(screen.getAllByRole('button', { name: /open your library/i })[0]);
    expect(await screen.findByText('LIBRARY')).toBeInTheDocument();
    expect(window.location.hash).toBe('#/library');
  });

  it('follows a welcome hash change without sending it through library routing', async () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    renderApp();
    expect(screen.getByText('LIBRARY')).toBeInTheDocument();
    window.location.hash = '#/welcome';
    expect(await screen.findAllByRole('button', { name: /open your library/i })).not.toHaveLength(0);
    expect(window.location.hash).toBe('#/welcome');
  });

  it('follows popstate when browser history leaves the welcome page', async () => {
    authState.user = { id: 'u', email: 'a@b.co' };
    window.history.replaceState(null, '', '#/welcome');
    renderApp();
    expect(await screen.findAllByRole('button', { name: /open your library/i })).not.toHaveLength(0);
    window.history.replaceState(null, '', '#/library');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(await screen.findByText('LIBRARY')).toBeInTheDocument();
  });

  it('polls admission, releases the gate when admitted, and stops polling after unmount', async () => {
    vi.useFakeTimers();
    authState.user = { id: 'u', email: 'a@b.co' };
    authState.admitted = false;
    authState.refreshAdmission.mockImplementation(async () => { authState.admitted = true; });
    const view = renderApp();
    expect(screen.getByRole('heading', { name: "You're in the queue" })).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(authState.refreshAdmission).toHaveBeenCalledOnce();

    view.rerender(<MotionRoot><App /></MotionRoot>);
    expect(screen.getByText('LIBRARY')).toBeInTheDocument();
    view.unmount();

    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(authState.refreshAdmission).toHaveBeenCalledOnce();
  });
});
