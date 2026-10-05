import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { m } from 'motion/react';
import { MotionRoot, Pressable, Sheet, Reveal, Stagger, StaggerItem, usePauseWhenHidden, useTilt } from './index';
import { motionPrefs } from '../test/setup';

describe('Pressable', () => {
  it('keeps button semantics: Enter and Space activate, disabled blocks', async () => {
    const onClick = vi.fn();
    render(<MotionRoot><Pressable onClick={onClick}>Go</Pressable><Pressable disabled onClick={onClick}>No</Pressable></MotionRoot>);
    const go = screen.getByRole('button', { name: 'Go' });
    go.focus();
    await userEvent.keyboard('{Enter}');
    await userEvent.keyboard(' ');
    expect(onClick).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it('renders an anchor with the given href when as="a"', () => {
    render(<MotionRoot><Pressable as="a" href="https://x.test" target="_blank" rel="noopener noreferrer">X</Pressable></MotionRoot>);
    expect(screen.getByRole('link', { name: 'X' })).toHaveAttribute('href', 'https://x.test');
  });

  it('still works with reduced motion', async () => {
    motionPrefs.reducedMotion = true;
    const onClick = vi.fn();
    render(<MotionRoot><Pressable onClick={onClick}>Go</Pressable></MotionRoot>);
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

function SheetHarness() {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLInputElement>(null);
  return (
    <MotionRoot>
      <button ref={opener} onClick={() => setOpen(true)}>Open</button>
      <button>Outside</button>
      <Sheet open={open} onClose={() => setOpen(false)} labelledBy="t" initialFocusRef={first} returnFocusRef={opener}>
        <h2 id="t">Title</h2>
        <input ref={first} aria-label="first" />
        <button>Last</button>
      </Sheet>
    </MotionRoot>
  );
}

describe('Sheet', () => {
  it('marks the backdrop and panel as centered', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));

    expect(screen.getByTestId('sheet-backdrop')).toHaveClass('motion-sheet-backdrop--centered');
    expect(screen.getByRole('dialog', { name: 'Title' })).toHaveClass('motion-sheet-panel--centered');
  });

  it('moves focus into the sheet synchronously when it opens', () => {
    const frame = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
    try {
      render(<SheetHarness />);
      const opener = screen.getByRole('button', { name: 'Open' });
      opener.focus();
      fireEvent.click(opener);

      expect(screen.getByLabelText('first')).toHaveFocus();
      expect(frame).not.toHaveBeenCalled();
    } finally {
      frame.mockRestore();
    }
  });

  it('keeps Tab inside the sheet during its opening transition', async () => {
    const frame = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
    try {
      render(<SheetHarness />);
      const opener = screen.getByRole('button', { name: 'Open' });
      opener.focus();
      fireEvent.click(opener);

      await userEvent.tab();
      expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
    } finally {
      frame.mockRestore();
    }
  });

  it('opens as a labelled modal dialog, focuses the first field, closes on Escape and returns focus', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    const dialog = await screen.findByRole('dialog', { name: 'Title' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByLabelText('first')).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Open' })).toHaveFocus();
  });

  it('traps Tab inside the sheet', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    await screen.findByRole('dialog');
    screen.getByRole('button', { name: 'Last' }).focus();
    await userEvent.tab();
    expect(screen.getByLabelText('first')).toHaveFocus();
  });

  it('closes on backdrop click', async () => {
    render(<SheetHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    await screen.findByRole('dialog');
    fireEvent.mouseDown(screen.getByTestId('sheet-backdrop'));
    fireEvent.click(screen.getByTestId('sheet-backdrop'));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('Reveal and Stagger', () => {
  it('renders reveal and stagger children', () => {
    render(<MotionRoot><Reveal><p>Revealed</p></Reveal><Reveal as="h2">A chapter</Reveal><Stagger><StaggerItem>First</StaggerItem><StaggerItem>Second</StaggerItem></Stagger></MotionRoot>);
    expect(screen.getByText('Revealed')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'A chapter' })).toBeInTheDocument();
    expect(screen.getByText('First')).toBeInTheDocument();
    expect(screen.getByText('Second')).toBeInTheDocument();
  });
});

function PauseHarness() {
  const ref = useRef<HTMLDivElement>(null);
  const shouldAnimate = usePauseWhenHidden(ref);
  return <div ref={ref} data-testid="pause-state">{String(shouldAnimate)}</div>;
}

describe('usePauseWhenHidden', () => {
  it('animates while visible and pauses when the document becomes hidden', async () => {
    render(<MotionRoot><PauseHarness /></MotionRoot>);
    expect(screen.getByTestId('pause-state')).toHaveTextContent('true');
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    fireEvent(document, new Event('visibilitychange'));
    expect(await screen.findByTestId('pause-state')).toHaveTextContent('false');
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  });
});

function TiltHarness() {
  const tilt = useTilt();
  return <m.div ref={tilt.ref} data-testid="tilt" style={tilt.style} onPointerMove={tilt.onPointerMove} onPointerLeave={tilt.onPointerLeave} />;
}

describe('useTilt', () => {
  it('ignores touch pointers', () => {
    render(<MotionRoot><TiltHarness /></MotionRoot>);
    const element = screen.getByTestId('tilt');
    const before = element.style.transform;
    fireEvent.pointerMove(element, { pointerType: 'touch', clientX: 100, clientY: 50 });
    expect(element.style.transform).toBe(before);
  });
});
