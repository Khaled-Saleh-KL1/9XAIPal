/**
 * Smoothing the display of a streamed answer.
 *
 * ## The problem
 *
 * The model does not emit text evenly. Measured over a real answer from
 * gemma4:31b-cloud: 77 token events, median 5 characters, 19% of them a single
 * character, and inter-event gaps of 79 ms at the median but 474 ms at p90 and
 * 751 ms at worst. Rendering each event the moment it lands reproduces that
 * cadence exactly: a letter, a long stall, then a clump of words. It reads as
 * broken even though the stream is perfectly healthy.
 *
 * ## The approach
 *
 * Arrival and display are decoupled. Incoming text goes into a buffer; a
 * animation-frame loop reveals it at a rate chosen to look steady.
 *
 * The key idea is the RESERVE: the pacer deliberately holds a small amount of
 * text back rather than showing everything it has. That cushion is what lets
 * it keep painting through a 750 ms stall: without it the buffer empties
 * during the first gap and the stutter returns, just shifted later. When the
 * buffer grows beyond the reserve (the model sent a burst) it drains fast to
 * catch up, so the cushion never becomes latency that accumulates.
 *
 * ⚠ This adds a fraction of a second of display latency by design. That is the
 * trade: text that arrives slightly later but flows, versus text that arrives
 * as soon as possible and lurches.
 */

/** Characters held back to ride out a stall. Sized from the measured p90 gap. */
const RESERVE_CHARS = 24;
/** Anything above the reserve is worked off over roughly this long. */
const DRAIN_MS = 400;
/** Trickle rate when the buffer is at or below the reserve, chars/second. */
const IDLE_CPS = 14;
/** Ceiling so a huge burst does not flash past unread. */
const MAX_CPS = 260;
/** Once the stream ends, empty the buffer at least this fast. */
const FINISH_CPS = 140;
/**
 * Minimum spacing between repaints. The reveal is smooth to the eye well below
 * frame rate, and each repaint re-parses the answer's markdown and KaTeX, so
 * there is no reason to pay for 60 of them a second.
 */
const MIN_REPAINT_MS = 55;

export interface Pacer {
  /** Feed newly arrived text. */
  push(text: string): void;
  /** No more text is coming; resolves once everything has been displayed. */
  finish(): Promise<void>;
  /** Abandon immediately, revealing whatever is buffered. */
  cancel(): void;
}

export function createPacer(onUpdate: (revealed: string) => void): Pacer {
  let revealed = '';
  let buffer = '';
  let streamEnded = false;
  let frame = 0;
  let lastTick = 0;
  let lastPaint = 0;
  let resolveDone: (() => void) | null = null;
  let carry = 0; // fractional characters owed from the previous frame

  const paint = (force: boolean) => {
    const now = performance.now();
    if (!force && now - lastPaint < MIN_REPAINT_MS) return;
    lastPaint = now;
    onUpdate(revealed);
  };

  const stop = () => {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    lastTick = 0;
  };

  const tick = (now: number) => {
    frame = 0;
    const dt = lastTick ? Math.min((now - lastTick) / 1000, 0.25) : 0;
    lastTick = now;

    if (dt > 0 && buffer.length) {
      const rate = streamEnded
        ? Math.max(FINISH_CPS, buffer.length / (DRAIN_MS / 1000))
        : Math.min(
            MAX_CPS,
            Math.max(IDLE_CPS, (buffer.length - RESERVE_CHARS) / (DRAIN_MS / 1000)),
          );

      carry += rate * dt;
      const take = Math.floor(carry);
      if (take > 0) {
        carry -= take;
        revealed += buffer.slice(0, take);
        buffer = buffer.slice(take);
        paint(false);
      }
    }

    if (streamEnded && !buffer.length) {
      paint(true);
      stop();
      resolveDone?.();
      resolveDone = null;
      return;
    }
    schedule();
  };

  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(tick);
  };

  return {
    push(text: string) {
      if (!text) return;
      buffer += text;
      schedule();
    },
    finish() {
      streamEnded = true;
      if (!buffer.length) {
        paint(true);
        stop();
        return Promise.resolve();
      }
      schedule();
      return new Promise<void>((resolve) => { resolveDone = resolve; });
    },
    cancel() {
      revealed += buffer;
      buffer = '';
      streamEnded = true;
      paint(true);
      stop();
      resolveDone?.();
      resolveDone = null;
    },
  };
}

/**
 * Hide a trailing, not-yet-closed math span while the answer is still arriving.
 *
 * Without this the reader watches raw LaTeX type itself out: "$\mathcal{P",
 * and then snap into a rendered symbol once the closing delimiter lands. The
 * flicker is worse than a brief gap, so the incomplete fragment is withheld
 * until it can be rendered.
 */
export function maskIncompleteMath(text: string): string {
  const display = text.lastIndexOf('$$');
  if (display !== -1) {
    // An odd number of $$ means the last one opened a block still being written.
    const opens = text.split('$$').length - 1;
    if (opens % 2 === 1) return text.slice(0, display);
  }
  // Same test for single-$ inline math, ignoring the $$ pairs already handled.
  const singles = (text.match(/(?<!\$)\$(?!\$)/g) || []).length;
  if (singles % 2 === 1) {
    const last = text.search(/(?<!\$)\$(?!\$)(?![\s\S]*(?<!\$)\$(?!\$))/);
    if (last !== -1) return text.slice(0, last);
  }
  return text;
}
