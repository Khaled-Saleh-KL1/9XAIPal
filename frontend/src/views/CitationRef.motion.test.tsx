import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { ImageLightbox } from '../components/ImageLightbox';

const mocks = vi.hoisted(() => ({ getChunk: vi.fn() }));

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  getChunk: mocks.getChunk,
}));

import { CitationRef } from './CitationRef';

describe('CitationRef disclosure', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getChunk.mockResolvedValue({ content_markdown: 'Quoted passage.' });
  });

  it('keeps the existing chip name and toggles its passage', async () => {
    render(
      <MotionRoot>
        <CitationRef cite={{ paper: 2, document_id: 'paper-2', label: 'Paper Two', sequence_id: 41 }} />
      </MotionRoot>,
    );
    const chip = screen.getByRole('button', { name: 'P2:41' });
    expect(chip).toHaveAccessibleName('P2:41');
    expect(chip).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(chip);
    expect(await screen.findByText('Quoted passage.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'P2:41' })).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'P2:41' }));
    await waitFor(() => expect(screen.queryByText('Quoted passage.')).not.toBeInTheDocument());
    expect(mocks.getChunk).toHaveBeenCalledTimes(1);
  });

  it('closes the open passage on Escape without leaving the chip open', async () => {
    render(
      <MotionRoot>
        <CitationRef cite={{ paper: 2, document_id: 'paper-2', label: 'Paper Two', sequence_id: 41 }} />
      </MotionRoot>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'P2:41' }));
    expect(await screen.findByText('Quoted passage.')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByText('Quoted passage.')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'P2:41' })).toHaveAttribute('aria-expanded', 'false');
    });
  });

  it('closes the open passage when a pointer goes outside the citation', async () => {
    render(
      <MotionRoot>
        <>
          <CitationRef cite={{ paper: 2, document_id: 'paper-2', label: 'Paper Two', sequence_id: 41 }} />
          <button type="button">Elsewhere</button>
        </>
      </MotionRoot>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'P2:41' }));
    expect(await screen.findByText('Quoted passage.')).toBeInTheDocument();

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Elsewhere' }));

    await waitFor(() => {
      expect(screen.queryByText('Quoted passage.')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'P2:41' })).toHaveAttribute('aria-expanded', 'false');
    });
  });

  it('lets the lightbox receive Escape before closing the citation', async () => {
    mocks.getChunk.mockResolvedValueOnce({ content_markdown: '![Cited figure](/figure.png)' });
    render(
      <MotionRoot>
        <ImageLightbox />
        <CitationRef cite={{ paper: 2, document_id: 'paper-2', label: 'Paper Two', sequence_id: 41 }} />
      </MotionRoot>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'P2:41' }));
    const image = await screen.findByAltText('Cited figure');
    Object.defineProperty(image, 'naturalWidth', { configurable: true, value: 120 });
    fireEvent.click(image);
    expect(await screen.findByRole('dialog', { name: 'Cited figure' })).toBeInTheDocument();

    fireEvent.keyDown(image, { key: 'Escape' });

    await waitFor(() => expect(document.querySelector('.lightbox-backdrop')).toBeNull());
    expect(screen.getByRole('button', { name: 'P2:41' })).toHaveAttribute('aria-expanded', 'true');
  });
});
