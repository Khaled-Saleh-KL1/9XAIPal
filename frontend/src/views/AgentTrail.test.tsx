import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AgentStep } from '../api';
import { PageMapProvider, buildPageMapFromPairs } from '../lib/pageMap';
import { MotionRoot } from '../motion';
import { Reasoning } from './AgentTrail';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  return {
    ...actual,
    AnimatePresence: ({ children }: { children: import('react').ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    useInView: () => true,
  };
});

const step = (overrides: Partial<AgentStep> = {}): AgentStep => ({
  id: 'step-1',
  n: 1,
  tool: 'SECTION',
  arg: '§3.2',
  state: 'done',
  think: 'The paper defines the setup here.',
  label: 'Section 3.2',
  result: '12 blocks · ¶31–¶42',
  seqs: [31, 32, 33, 34, 35, 36, 37, 38, 39],
  sources: [],
  ...overrides,
});

function view(steps: AgentStep[], props: { live?: boolean; writing?: boolean; onJump?: (seq: number) => void } = {}) {
  return render(
    <MotionRoot>
      <PageMapProvider value={buildPageMapFromPairs([[31, 4], [32, 4], [33, 4], [34, 5], [35, 6], [36, 7], [37, 8], [38, 9], [39, 10]])}>
        <Reasoning steps={steps} {...props} />
      </PageMapProvider>
    </MotionRoot>,
  );
}

describe('Reasoning rounds', () => {
  afterEach(() => vi.useRealTimers());

  it('groups steps by round, starts collapsed, and reveals step details, sources, and counts', async () => {
    const onJump = vi.fn();
    const steps = [
      step(),
      step({
        id: 'step-2',
        tool: 'WEB',
        arg: 'https://docs.example.com/guide',
        think: null,
        label: 'Documentation',
        result: 'Found the official guide',
        seqs: [],
        sources: [{ title: 'Official guide', url: 'https://www.docs.example.com/guide' }],
      }),
      step({
        id: 'step-3',
        n: 2,
        tool: 'SEARCH',
        arg: 'positional encoding',
        think: null,
        label: 'Paper search',
        result: '3 matches',
        seqs: [],
      }),
    ];

    view(steps, { onJump });

    const firstRound = screen.getByRole('button', { name: 'The paper defines the setup here.' });
    const secondRound = screen.getByRole('button', { name: 'See reasoning' });
    expect(firstRound).toHaveAttribute('aria-expanded', 'false');
    expect(secondRound).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getAllByRole('button')).toHaveLength(2);
    expect(screen.getByText('2 from the paper · 1 from the web')).toBeInTheDocument();

    fireEvent.click(firstRound);
    expect(firstRound).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText('Section 3.2')).toBeInTheDocument();
    expect(screen.getByText('12 blocks · ¶31–¶42')).toBeInTheDocument();
    expect(screen.getByText('Documentation')).toBeInTheDocument();
    expect(screen.getByText('Found the official guide')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '→ docs.example.com' })).toHaveAttribute(
      'href',
      'https://www.docs.example.com/guide',
    );
    // Citations on page 4 collapse before the six-chip cap is applied.
    expect(screen.getAllByRole('button', { name: 'p. 4' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'p. 4' })).toHaveAttribute('title', 'Page 4, paragraphs 31, 32, 33');
    fireEvent.click(screen.getByRole('button', { name: 'p. 4' }));
    expect(onJump).toHaveBeenCalledWith(31);
    expect(screen.getByText('+1')).toBeInTheDocument();

    fireEvent.click(secondRound);
    expect(secondRound).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText('Paper search')).toBeInTheDocument();
    expect(screen.getByText('3 matches')).toBeInTheDocument();
  });

  it('keeps the round chevron and marks it rotated when expanded', () => {
    view([step()]);

    const toggle = screen.getByRole('button', { name: 'The paper defines the setup here.' });
    const chevron = toggle.querySelector('.trail-round-caret');
    expect(chevron).toHaveTextContent('›');
    expect(chevron).not.toHaveClass('is-expanded');
    expect(chevron).toHaveAttribute('aria-hidden', 'true');

    fireEvent.click(toggle);
    expect(chevron).toHaveTextContent('›');
    expect(chevron).toHaveClass('is-expanded');
  });

  it('marks expanded web source links for quiet styling', () => {
    view([step({
      tool: 'WEB',
      arg: 'https://docs.example.com/guide',
      think: null,
      label: 'Documentation',
      result: 'Found the official guide',
      seqs: [],
      sources: [{ title: 'Official guide', url: 'https://www.docs.example.com/guide' }],
    })]);

    fireEvent.click(screen.getByRole('button', { name: 'See reasoning' }));
    expect(screen.getByRole('link', { name: '→ docs.example.com' })).toHaveClass('trail-source-link');
  });

  it('folds rounds after six and reveals the remaining rows', () => {
    const steps = Array.from({ length: 8 }, (_, index) => step({
      id: `step-${index + 1}`,
      n: index + 1,
      think: `Round ${index + 1}`,
      seqs: [],
    }));
    const { container } = view(steps);

    expect(container.querySelectorAll('.trail-round-toggle')).toHaveLength(6);
    const more = screen.getByRole('button', { name: '+2 more steps' });
    fireEvent.click(more);
    expect(container.querySelectorAll('.trail-round-toggle')).toHaveLength(8);
    expect(screen.getByRole('button', { name: 'Round 8' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('shows the mascot and Thinking when live before any steps arrive', () => {
    view([], { live: true });

    expect(screen.getByTestId('thinking-mark')).toBeInTheDocument();
    expect(screen.getByText('Thinking…')).toBeInTheDocument();
  });

  it.each([
    ['SECTION', '§3.2', 'Reading §3.2…'],
    ['READ', '§3.2', 'Reading §3.2…'],
    ['SEARCH', 'positional encoding', 'Searching “positional encoding”…'],
    ['WEB', 'https://docs.example.com', 'Reading the web…'],
    ['NOTE', 'note-id', 'Pinning a note…'],
    ['REMEMBER', 'memory', 'Remembering…'],
  ] as const)('maps running %s steps to their status text', (_tool, arg, expected) => {
    const tool = _tool as AgentStep['tool'];
    view([step({ tool, arg, state: 'running', result: '' })], { live: true });

    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it('shows writing status and switches to Almost there after eight seconds', async () => {
    vi.useFakeTimers();
    view([step()], { live: true, writing: true });

    expect(screen.getByText('Writing the answer…')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(screen.getByText('Almost there…')).toBeInTheDocument();
  });

  it('removes the live mascot when work ends and keeps the collapsed rounds', async () => {
    const steps = [step()];
    const result = view(steps, { live: true });
    expect(screen.getByTestId('thinking-mark')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'The paper defines the setup here.' }));
    expect(await screen.findByText('Section 3.2')).toBeInTheDocument();

    result.rerender(
      <MotionRoot>
        <PageMapProvider value={buildPageMapFromPairs([[31, 4]])}>
          <Reasoning steps={steps} live={false} />
        </PageMapProvider>
      </MotionRoot>,
    );

    await waitFor(() => expect(screen.queryByTestId('thinking-mark')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'The paper defines the setup here.' })).toHaveAttribute('aria-expanded', 'false');
  });
});
