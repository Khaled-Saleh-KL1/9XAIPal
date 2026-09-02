import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

/**
 * In-app replacement for `window.confirm`.
 *
 * The browser's dialog is drawn by the browser, in its own font, pinned to
 * the top of the window with no relation to the app it interrupts — and it
 * blocks the main thread while it's up. This renders inside the app instead,
 * themed like everything else, so a destructive confirmation looks like part
 * of the product.
 *
 * The API is deliberately promise-shaped so every existing call site keeps
 * its structure — `if (!window.confirm(...)) return;` becomes
 * `if (!(await confirm({...}))) return;` and nothing else about the handler
 * has to move.
 */

export type ConfirmOptions = {
  title: string;
  /** Optional second paragraph: the consequences, spelled out. */
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 'danger' paints the confirm button in the destructive tone. */
  tone?: 'danger' | 'default';
};

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used inside <ConfirmProvider>');
  return ctx;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  const confirm = useCallback<ConfirmFn>((next) => {
    // A second confirm while one is open would strand the first promise and
    // leave its caller waiting forever. Resolve it as a cancel first.
    resolveRef.current?.(false);
    setOptions(next);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    resolveRef.current?.(value);
    resolveRef.current = null;
    setOptions(null);
  }, []);

  useEffect(() => {
    if (!options) return;
    confirmButtonRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') settle(false);
      if (e.key === 'Enter') settle(true);
    };
    window.addEventListener('keydown', onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [options, settle]);

  // If the provider unmounts with a dialog open, don't leave the caller hanging.
  useEffect(() => () => resolveRef.current?.(false), []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {options && (
        <div
          className="confirm-backdrop"
          onClick={() => settle(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-title"
        >
          <div className="confirm-card" onClick={(e) => e.stopPropagation()}>
            <h2 className="confirm-title" id="confirm-title">
              {options.title}
            </h2>
            {options.body && <p className="confirm-body">{options.body}</p>}
            <div className="confirm-actions">
              <button type="button" className="confirm-cancel" onClick={() => settle(false)}>
                {options.cancelLabel ?? 'Cancel'}
              </button>
              <button
                type="button"
                ref={confirmButtonRef}
                className={
                  options.tone === 'danger' ? 'confirm-go is-danger' : 'confirm-go'
                }
                onClick={() => settle(true)}
              >
                {options.confirmLabel ?? 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
