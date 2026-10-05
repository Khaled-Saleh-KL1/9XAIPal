import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Sticky } from '../api';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  reducedMotion: false,
  rows: new Map<string, { props: Record<string, any>; mountId: number }>(),
  stickyRows: new Map<string, Record<string, any>>(),
  nextMountId: 0,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const mountId = React.useRef<number | null>(null);
    if (mountId.current === null) mountId.current = ++mocks.nextMountId;
    const key = props['data-note-motion-key'];
    if (props['data-testid'] === 'wall-motion-item' && typeof key === 'string') {
      mocks.rows.set(key, { props, mountId: mountId.current });
    }
    if (props['data-testid'] === 'sticky-motion-wrapper' && typeof props['data-motion-key'] === 'string') {
      mocks.stickyRows.set(props['data-motion-key'], props);
    }
    return React.createElement(actual.m.div, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      return key === 'div' ? InspectableDiv : Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m: inspectedM, useReducedMotion: () => mocks.reducedMotion, useInView: () => true };
});

import { NoteWall } from './NoteWall';

const sticky = (id: string, body: string, origin: Sticky['origin'] = 'user'): Sticky => ({
  id,
  body,
  color: 'blue',
  pinned: true,
  board: 'universal',
  scope: 'library',
  origin,
  author_model: null,
  papers: [],
  created_at: null,
  updated_at: null,
});

function wall(notes: Sticky[]) {
  return (
    <MotionRoot>
      <NoteWall notes={notes} onCreate={vi.fn()} onSave={vi.fn()} onDelete={vi.fn()} />
    </MotionRoot>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.rows.clear();
  mocks.stickyRows.clear();
  mocks.nextMountId = 0;
  mocks.reducedMotion = false;
});

describe('NoteWall motion', () => {
  it('filters cards, reflows the wall, and does not replay first-mount entrance on updates', async () => {
    const notes = [sticky('alpha', 'Alpha finding'), sticky('beta', 'Beta finding', 'assistant')];
    render(wall(notes));
    expect(screen.getByText('Alpha finding')).toBeInTheDocument();
    expect(screen.getByText('Beta finding')).toBeInTheDocument();
    expect(mocks.rows.get('alpha')?.props.initial).toMatchObject({ opacity: 0, rotateX: -70 });
    const alphaMount = mocks.rows.get('alpha')?.mountId;

    fireEvent.change(screen.getByRole('textbox', { name: 'Find a note' }), { target: { value: 'alpha' } });

    expect(screen.getByText('Alpha finding')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Beta finding')).not.toBeInTheDocument(), { timeout: 3000 });
    expect(mocks.rows.get('alpha')?.mountId).toBe(alphaMount);
    expect(mocks.rows.get('alpha')?.props.initial).toBe(false);
  });

  it('uses no flip or pointer tilt transforms under reduced motion', () => {
    mocks.reducedMotion = true;
    render(wall([sticky('still-wall', 'Still note')]));

    const props = mocks.rows.get('still-wall')?.props;
    expect(props?.initial).toBe(false);
    expect(props?.animate).toEqual({ opacity: 1 });
    expect(props?.style).toBeUndefined();
    expect(props?.layout).toBe(false);
  });

  it('keeps a pinned note still while its text is being edited', async () => {
    render(wall([sticky('edit-wall', 'Edit this wall note')]));
    fireEvent.click(screen.getByText('Edit this wall note'));
    const textarea = await screen.findByPlaceholderText('Write it down…');
    await waitFor(() => expect(textarea).toHaveFocus());

    const props = mocks.stickyRows.get('edit-wall');
    expect(props?.drag).toBe(false);
    expect(props?.onPointerMove).toBeUndefined();
    expect(props?.onPointerLeave).toBeUndefined();
    expect(props?.style).toBeUndefined();
    fireEvent.pointerMove(textarea, { pointerType: 'mouse', clientX: 80, clientY: 20 });
    expect(textarea).toHaveValue('Edit this wall note');
  });
});
