import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  listStudies: vi.fn(),
  listPapers: vi.fn(),
  listModels: vi.fn(),
  getStudy: vi.fn(),
  getStudyChat: vi.fn(),
  listStickies: vi.fn(),
  listStudyConversations: vi.fn(),
  studyRows: new Map<string, Record<string, any>>(),
  indicatorProps: undefined as Record<string, any> | undefined,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableButton = React.forwardRef<HTMLButtonElement, Record<string, any>>((props, ref) => {
    const id = props['data-study-id'];
    if (props['data-testid'] === 'study-rail-row' && typeof id === 'string') mocks.studyRows.set(id, props);
    return React.createElement(actual.m.button, { ...props, ref });
  });
  const InspectableSpan = React.forwardRef<HTMLSpanElement, Record<string, any>>((props, ref) => {
    if (props['data-testid'] === 'active-study-indicator') mocks.indicatorProps = props;
    return React.createElement(actual.m.span, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      if (key === 'button') return InspectableButton;
      if (key === 'span') return InspectableSpan;
      return Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m: inspectedM, useReducedMotion: () => false };
});

vi.mock('../api', () => ({
  LIBRARY_SCOPE: 'library',
  listStudies: mocks.listStudies,
  listPapers: mocks.listPapers,
  listModels: mocks.listModels,
  getStudy: mocks.getStudy,
  getStudyChat: mocks.getStudyChat,
  listStickies: mocks.listStickies,
  listStudyConversations: mocks.listStudyConversations,
}));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../components/UserMenu', () => ({ UserMenuInline: () => null }));
vi.mock('./NoteWall', () => ({ NoteWall: () => null }));
vi.mock('./PaperPicker', () => ({ PaperPicker: () => null }));
vi.mock('./StickyBoard', () => ({ StickyBoard: () => null }));
vi.mock('./StudyChat', () => ({ StudyChat: () => null }));

import { DeskView } from './DeskView';

const studies = [
  { id: 'study-a', name: 'Alpha study', description: null, paper_count: 2, created_at: null, updated_at: null },
  { id: 'study-b', name: 'Beta study', description: null, paper_count: 1, created_at: null, updated_at: null },
];

describe('DeskView study rail motion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.studyRows.clear();
    mocks.indicatorProps = undefined;
    mocks.listStudies.mockResolvedValue(studies);
    mocks.listPapers.mockResolvedValue([]);
    mocks.listModels.mockResolvedValue({ models: [] });
    mocks.getStudy.mockImplementation(async (id: string) => ({ study: studies.find((s) => s.id === id)!, papers: [] }));
    mocks.getStudyChat.mockResolvedValue({ conversation_id: null, turns: [] });
    mocks.listStickies.mockResolvedValue([]);
    mocks.listStudyConversations.mockResolvedValue([]);
  });

  it('tilts study rows, presses with a spring, and moves the active indicator between studies', async () => {
    render(
      <MotionRoot>
        <DeskView page="study" initialScope="study-a" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const alpha = await screen.findByRole('button', { name: /Alpha study/ });
    await waitFor(() => expect(mocks.studyRows.get('study-a')?.className).toContain('is-on'));
    const alphaMotion = mocks.studyRows.get('study-a');
    expect(alphaMotion?.whileTap).toBeDefined();
    expect(alphaMotion?.style?.rotateX).toBeDefined();
    expect(alphaMotion?.style?.rotateY).toBeDefined();
    const rect = vi.spyOn(alpha, 'getBoundingClientRect').mockReturnValue({
      left: 0, right: 100, top: 0, bottom: 40, width: 100, height: 40,
    } as DOMRect);
    fireEvent.pointerMove(alpha, { pointerType: 'mouse', clientX: 90, clientY: 8 });
    expect(rect).toHaveBeenCalled();
    expect(screen.getByTestId('active-study-indicator')).toBeInTheDocument();
    expect(mocks.indicatorProps?.layoutId).toBe('active-study-indicator');

    fireEvent.click(screen.getByRole('button', { name: /Beta study/ }));
    await waitFor(() => expect(mocks.studyRows.get('study-b')?.className).toContain('is-on'));
    expect(screen.getByTestId('active-study-indicator').parentElement).toBe(screen.getByRole('button', { name: /Beta study/ }));
    expect(alpha).toBeInTheDocument();
  });
});
