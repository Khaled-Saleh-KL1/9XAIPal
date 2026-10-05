import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Sticky } from '../api';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  reducedMotion: false,
  rows: new Map<string, { props: Record<string, any>; mountId: number }>(),
  nextMountId: 0,
  save: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const mountId = React.useRef<number | null>(null);
    if (mountId.current === null) mountId.current = ++mocks.nextMountId;
    const key = props['data-motion-key'];
    if (props['data-testid'] === 'sticky-motion-wrapper' && typeof key === 'string') {
      mocks.rows.set(key, { props, mountId: mountId.current });
    }
    return React.createElement(actual.m.div, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      return key === 'div' ? InspectableDiv : Reflect.get(target, key, receiver);
    },
  });
  return {
    ...actual,
    m: inspectedM,
    useReducedMotion: () => mocks.reducedMotion,
    useInView: () => true,
  };
});

import { StickyBoard } from './StickyBoard';

const sticky = (id: string, body: string): Sticky => ({
  id,
  body,
  color: 'yellow',
  pinned: false,
  board: 'chat',
  scope: 'study-1',
  origin: 'user',
  author_model: null,
  papers: [],
  created_at: null,
  updated_at: null,
});

function board(notes: Sticky[], overrides: Partial<React.ComponentProps<typeof StickyBoard>> = {}) {
  return (
    <MotionRoot>
      <StickyBoard
        notes={notes}
        scopeName="Study"
        collapsed={false}
        onToggle={vi.fn()}
        onCreate={vi.fn()}
        onSave={mocks.save}
        onDelete={mocks.remove}
        {...overrides}
      />
    </MotionRoot>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.rows.clear();
  mocks.nextMountId = 0;
  mocks.reducedMotion = false;
});

describe('StickyBoard motion', () => {
  it('keeps a pointer drag from editing, deleting, saving, or reordering a note', () => {
    const notes = [sticky('one', 'First note'), sticky('two', 'Second note')];
    const { container } = render(board(notes));
    const list = container.querySelector('.board-list')!;
    const before = [...list.querySelectorAll('.sticky-body')].map((el) => el.textContent);
    const target = screen.getByText('First note');

    fireEvent.pointerDown(target, { pointerId: 1, button: 0, clientX: 20, clientY: 30 });
    fireEvent.pointerMove(target, { pointerId: 1, buttons: 1, clientX: 60, clientY: 30 });
    fireEvent.pointerUp(target, { pointerId: 1, button: 0, clientX: 60, clientY: 30 });

    expect([...list.querySelectorAll('.sticky-body')].map((el) => el.textContent)).toEqual(before);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.remove).not.toHaveBeenCalled();
    expect(mocks.rows.get('one')?.props.drag).toBe(true);
  });

  it('opens the editor on a click and disables drag while editing', async () => {
    render(board([sticky('editable', 'Edit me')]));
    fireEvent.click(screen.getByText('Edit me'));

    const textarea = screen.getByRole('textbox');
    await waitFor(() => expect(textarea).toHaveFocus());
    expect(mocks.rows.get('editable')?.props.drag).toBe(false);
  });

  it('requires two delete clicks, then exits the removed note and calls delete once', async () => {
    function Harness() {
      const [notes, setNotes] = useState([sticky('delete-me', 'Delete target')]);
      return board(notes, {
        onDelete: (id) => {
          mocks.remove(id);
          setNotes((current) => current.filter((item) => item.id !== id));
        },
      });
    }
    render(<Harness />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete this note' }));
    expect(mocks.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    expect(mocks.remove).toHaveBeenCalledTimes(1);
    expect(mocks.remove).toHaveBeenCalledWith('delete-me');
    await waitFor(() => expect(screen.queryByText('Delete target')).not.toBeInTheDocument(), { timeout: 3000 });
  });

  it('focuses a newly added sticky and gives its inner motion wrapper a pop entrance', async () => {
    function Harness() {
      const [notes, setNotes] = useState<Sticky[]>([]);
      return board(notes, { onCreate: () => setNotes([sticky('new-note', '')]) });
    }
    render(<Harness />);
    fireEvent.click(screen.getByTitle('New note'));

    const textarea = await screen.findByPlaceholderText('Write it down…');
    await waitFor(() => expect(textarea).toHaveFocus());
    expect(mocks.rows.get('new-note')?.props.initial).toMatchObject({ scale: 0.5, rotate: -8, y: 12 });
  });

  it('removes sway, hover wobble, tilt and drag transforms under reduced motion', () => {
    mocks.reducedMotion = true;
    render(board([sticky('still', 'No motion') ]));

    const props = mocks.rows.get('still')?.props;
    expect(props?.initial).toBe(false);
    expect(props?.animate).toEqual({ opacity: 1 });
    expect(props?.whileHover).toBeUndefined();
    expect(props?.whileDrag).toBeUndefined();
    expect(props?.drag).toBe(false);
    expect(props?.layout).toBe(false);
  });
});
