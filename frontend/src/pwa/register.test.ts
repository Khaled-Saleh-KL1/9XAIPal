import { describe, expect, it, vi } from 'vitest';
import { setupUpdateReload } from './register';

function fakes(controller: object | null, state: 'visible' | 'hidden' = 'visible') {
  const sw: Record<string, () => void> = {};
  const doc: Record<string, () => void> & { visibilityState: string } = { visibilityState: state } as any;
  const reload = vi.fn();
  setupUpdateReload(
    { controller, addEventListener: (t: string, h: () => void) => { sw[t] = h; } } as any,
    { get visibilityState() { return doc.visibilityState as DocumentVisibilityState; }, addEventListener: (t: string, h: () => void) => { doc[t] = h; } } as any,
    reload,
  );
  const setVis = (v: 'visible' | 'hidden') => { doc.visibilityState = v; doc.visibilitychange(); };
  return { sw, setVis, reload };
}

describe('setupUpdateReload', () => {
  it('does not reload on the very first install (no previous controller)', () => {
    const f = fakes(null);
    f.sw.controllerchange();
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).not.toHaveBeenCalled();
  });
  it('does not reload while the user is in the page', () => {
    const f = fakes({});
    f.sw.controllerchange();
    expect(f.reload).not.toHaveBeenCalled();
  });
  it('reloads exactly once, when the page returns from hidden to visible', () => {
    const f = fakes({});
    f.sw.controllerchange();
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).toHaveBeenCalledTimes(1);
    f.sw.controllerchange();
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).toHaveBeenCalledTimes(1);
  });
  it('does nothing without a pending update', () => {
    const f = fakes({});
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).not.toHaveBeenCalled();
  });
});
