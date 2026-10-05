import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  listStudies: vi.fn(),
  listPapers: vi.fn(),
  listModels: vi.fn(),
}));

vi.mock('../api', () => ({
  LIBRARY_SCOPE: 'library',
  listStudies: mocks.listStudies,
  listPapers: mocks.listPapers,
  listModels: mocks.listModels,
}));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../components/UserMenu', () => ({ UserMenuInline: () => null }));
vi.mock('./NoteWall', () => ({ NoteWall: () => null }));
vi.mock('./PaperPicker', () => ({ PaperPicker: () => null }));
vi.mock('./StickyBoard', () => ({ StickyBoard: () => null }));
vi.mock('./StudyChat', () => ({ StudyChat: () => null }));

import { DeskView } from './DeskView';

function setMobileMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
}

describe('DeskView mobile rail motion', () => {
  beforeEach(() => {
    mocks.listStudies.mockResolvedValue([]);
    mocks.listPapers.mockResolvedValue([]);
    mocks.listModels.mockResolvedValue({ models: [] });
    setMobileMatchMedia(false);
  });

  afterEach(() => {
    Reflect.deleteProperty(window, 'matchMedia');
  });

  it('keeps the desktop study rail interactive when the mobile drawer is closed', () => {
    const { container } = render(
      <MotionRoot>
        <DeskView page="study" initialScope="library" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const rail = container.querySelector('.rail');
    expect(rail).not.toBeNull();
    expect(rail).not.toHaveAttribute('inert');
    expect(rail).not.toHaveAttribute('aria-hidden', 'true');
  });

  it('makes a closed mobile drawer inert until the reader opens it', () => {
    setMobileMatchMedia(true);
    const { container } = render(
      <MotionRoot>
        <DeskView page="study" initialScope="library" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const rail = container.querySelector('.rail');
    expect(rail).toHaveAttribute('inert');
    expect(rail).toHaveAttribute('aria-hidden', 'true');
  });
});
