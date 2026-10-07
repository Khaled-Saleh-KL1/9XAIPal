import { describe, expect, it, vi } from 'vitest';
import { setupUpdateReload } from './register';

function fakes(controller: object | null, state: 'visible' | 'hidden' = 'visible') {
  const sw: Record<string, () => void> = {};
  const doc: Record<string, () => void> & { visibilityState: string } = { visibilityState: state } as any;
  const reload = vi.fn();
  const busy = { busy: false };
  setupUpdateReload(
    { controller, addEventListener: (t: string, h: () => void) => { sw[t] = h; } } as any,
    { get visibilityState() { return doc.visibilityState as DocumentVisibilityState; }, addEventListener: (t: string, h: () => void) => { doc[t] = h; } } as any,
    reload,
    { isBusy: () => busy.busy },
  );
  const setVis = (v: 'visible' | 'hidden') => { doc.visibilityState = v; doc.visibilitychange(); };
  const setBusy = (b: boolean) => { busy.busy = b; };
  return { sw, setVis, reload, setBusy };
}

describe('setupUpdateReload', () => {
  it('ignores only the first controllerchange of a first install, not later ones', () => {
    const f = fakes(null);
    f.sw.controllerchange(); // clients.claim() on first install
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).not.toHaveBeenCalled();
    f.sw.controllerchange(); // a later deploy in the same long-lived tab
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).toHaveBeenCalledTimes(1);
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
  it('never reloads while busy, nor the moment work finishes; waits for the next return', () => {
    const f = fakes({});
    f.sw.controllerchange();
    f.setBusy(true);
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).not.toHaveBeenCalled();
    // The answer just finished and the reader is looking at it: do not reload under them.
    f.setBusy(false);
    expect(f.reload).not.toHaveBeenCalled();
    f.setVis('hidden'); f.setVis('visible');
    expect(f.reload).toHaveBeenCalledTimes(1);
  });
  it('does not reload on idle if the user never left the page', () => {
    const f = fakes({});
    f.sw.controllerchange();
    f.setBusy(true);
    f.setBusy(false);
    expect(f.reload).not.toHaveBeenCalled();
  });
});
