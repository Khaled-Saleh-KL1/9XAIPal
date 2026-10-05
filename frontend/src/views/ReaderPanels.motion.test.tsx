import { useRef, useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GroundingReport, PaperMeta } from '../api';
import { MotionRoot } from '../motion';
import { ImageLightbox } from '../components/ImageLightbox';
import { EvidencePanel } from './EvidencePanel';
import { MarginaliaPanel } from './MarginaliaPanel';
import { PaperPicker } from './PaperPicker';

const outline = [{ sequence_order: 1, text: 'Introduction', level: 1 }];

function renderMarginalia(onClose = vi.fn()) {
  function Harness() {
    const [open, setOpen] = useState(false);
    const openerRef = useRef<HTMLButtonElement>(null);
    return (
      <>
        <button ref={openerRef} type="button" onClick={() => setOpen(true)}>Open notes</button>
        <MarginaliaPanel
          open={open}
          tab="contents"
          onTabChange={() => {}}
          onClose={() => { onClose(); setOpen(false); }}
          outline={outline}
          bookmarks={[]}
          rows={[]}
          onJump={() => {}}
          onRemoveBookmark={() => {}}
          onAddBookmark={() => {}}
          currentSeq={null}
          returnFocusRef={openerRef}
        />
      </>
    );
  }
  const view = render(<MotionRoot><Harness /></MotionRoot>);
  return { ...view, onClose };
}

const paper = (id: string, title: string): PaperMeta => ({
  id,
  filename: `${id}.pdf`,
  original_filename: `${id}.pdf`,
  title,
  file_size_bytes: 1000,
  page_count: 4,
  status: 'complete',
  error_message: null,
  created_at: '2026-01-01T00:00:00.000Z',
  updated_at: null,
  doc_kind: 'paper',
});

const report: GroundingReport = {
  status: 'verified',
  claims: [{
    text: 'The result follows from the cited passage.',
    refs: [['paper-1', 1]],
    verdict: 'supported',
    evidence: { document_id: 'paper-1', sequence_id: 1, page: 2 },
    quote: 'The cited passage supports the result.',
    note: '',
  }],
  summary: { supported: 1 },
};

describe('reader panels and pickers motion', () => {
  it('opens Marginalia as a labelled modal, focuses search, traps Tab and returns focus after Escape', async () => {
    const user = userEvent.setup();
    renderMarginalia();

    const opener = screen.getByRole('button', { name: 'Open notes' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Contents, bookmarks and notes' });
    const search = within(dialog).getByPlaceholderText('Find a section…');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(search).toHaveFocus();

    const first = within(dialog).getByRole('button', { name: /^Contents/ });
    const last = within(dialog).getByRole('button', { name: 'Introduction' });
    first.focus();
    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(last).toHaveFocus();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it('leaves Marginalia open when Escape closes a lightbox above it', async () => {
    const onClose = vi.fn();
    render(
      <MotionRoot>
        <ImageLightbox />
        <MarginaliaPanel
          open
          tab="contents"
          onTabChange={() => {}}
          onClose={onClose}
          outline={outline}
          bookmarks={[]}
          rows={[]}
          onJump={() => {}}
          onRemoveBookmark={() => {}}
          onAddBookmark={() => {}}
          currentSeq={null}
        />
        <img data-testid="figure" src="/figure.png" alt="A figure" />
      </MotionRoot>,
    );
    const image = screen.getByTestId('figure');
    Object.defineProperty(image, 'naturalWidth', { configurable: true, value: 120 });
    fireEvent.click(image);
    expect(await screen.findByRole('dialog', { name: 'A figure' })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(document.querySelector('.lightbox-backdrop')).toBeNull());
    expect(screen.getByRole('dialog', { name: 'Contents, bookmarks and notes' })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('uses a labelled Sheet for paper selection and applies the selected paper before closing', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();

    function Harness() {
      const [open, setOpen] = useState(false);
      const openerRef = useRef<HTMLButtonElement>(null);
      return (
        <>
          <button ref={openerRef} type="button" onClick={() => setOpen(true)}>Choose papers</button>
          <PaperPicker
            open={open}
            library={[paper('paper-1', 'A paper about motion')]}
            chosen={[]}
            onApply={(ids) => { onApply(ids); setOpen(false); }}
            onClose={() => setOpen(false)}
            returnFocusRef={openerRef}
          />
        </>
      );
    }

    render(<MotionRoot><Harness /></MotionRoot>);
    const opener = screen.getByRole('button', { name: 'Choose papers' });
    await user.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'Papers in this study' });
    const search = within(dialog).getByRole('textbox', { name: 'Find a paper' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(search).toHaveFocus();

    await user.click(within(dialog).getByRole('button', { name: /A paper about motion/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));

    expect(onApply).toHaveBeenCalledWith(['paper-1']);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Papers in this study' })).toBeNull());
    expect(opener).toHaveFocus();
  });

  it('reveals evidence on demand and keeps aria-expanded in sync', async () => {
    const user = userEvent.setup();
    render(<MotionRoot><EvidencePanel report={report} /></MotionRoot>);

    const toggle = screen.getByRole('button', { name: '1 of 1 claim verified' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('The result follows from the cited passage.')).toBeNull();

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('The result follows from the cited passage.')).toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await waitFor(() => expect(screen.queryByText('The result follows from the cited passage.')).toBeNull());
  });
});
