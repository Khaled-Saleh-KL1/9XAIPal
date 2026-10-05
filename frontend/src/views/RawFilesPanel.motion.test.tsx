import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { RawFilesPanel } from './RawFilesPanel';

function RawFilesHarness() {
  const [open, setOpen] = useState(false);
  return (
    <MotionRoot>
      <button onClick={() => setOpen(true)}>Open raw files</button>
      <RawFilesPanel papers={[]} open={open} onClose={() => setOpen(false)} onOpenPdf={() => {}} />
    </MotionRoot>
  );
}

describe('RawFilesPanel modal behavior', () => {
  it('traps focus, closes on Escape, and returns focus to its opener', async () => {
    const user = userEvent.setup();
    render(<RawFilesHarness />);

    const opener = screen.getByRole('button', { name: 'Open raw files' });
    await user.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'Raw Files' });
    const search = screen.getByPlaceholderText('Search by title, author, or venue…');
    expect(search).toHaveFocus();

    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await vi.waitFor(() => expect(opener).toHaveFocus());
  });
});
