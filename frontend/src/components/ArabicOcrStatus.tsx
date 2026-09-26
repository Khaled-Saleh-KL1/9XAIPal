import type { ArabicWritingStyle } from '../api';

interface Props {
  errorCode?: string | null;
  errorMessage?: string | null;
  actionRequired?: string | null;
  allowedActions?: ArabicWritingStyle[];
  confirmationPending?: boolean;
  onConfirmWritingStyle?: (style: ArabicWritingStyle) => void;
}

export function ArabicOcrStatus({
  errorCode,
  errorMessage,
  actionRequired,
  allowedActions = [],
  confirmationPending = false,
  onConfirmWritingStyle,
}: Props) {
  if (errorCode === 'handwritten_arabic_unavailable' || errorCode === 'arabic_classifier_unavailable') {
    return (
      <div
        className="mx-3 mb-3 rounded-md px-3 py-2 text-[12px] leading-relaxed sm:mx-4"
        role="alert"
        style={{ background: 'var(--bg-2)', border: '1px solid var(--border-strong)', color: 'var(--fg)' }}
      >
        {errorMessage || (errorCode === 'arabic_classifier_unavailable'
          ? 'Start Ollama and make sure the configured vision model is available, then retry.'
          : 'Handwritten Arabic extraction is unavailable on this deployment.')}
      </div>
    );
  }

  if (actionRequired !== 'confirm_arabic_writing_style' || allowedActions.length === 0) return null;

  const confirm = (event: React.MouseEvent<HTMLButtonElement>, style: ArabicWritingStyle) => {
    event.stopPropagation();
    onConfirmWritingStyle?.(style);
  };

  return (
    <div
      className="mx-3 mb-3 rounded-md px-3 py-2 text-[12px] leading-relaxed sm:mx-4"
      role="group"
      aria-label="Confirm Arabic writing style"
      aria-busy={confirmationPending}
      style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--fg)' }}
    >
      <div className="font-medium">Confirm the Arabic writing style</div>
      {errorMessage && <div className="mt-1" style={{ color: 'var(--muted)' }}>{errorMessage}</div>}
      <div className="mt-2 flex flex-wrap gap-2">
        {allowedActions.includes('printed') && (
          <button
            type="button"
            disabled={confirmationPending || !onConfirmWritingStyle}
            onClick={(event) => confirm(event, 'printed')}
            className="rounded-md px-2.5 py-1 text-[11.5px] disabled:opacity-50"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)', border: '1px solid var(--border)' }}
          >
            Printed
          </button>
        )}
        {allowedActions.includes('handwritten') && (
          <button
            type="button"
            disabled={confirmationPending || !onConfirmWritingStyle}
            onClick={(event) => confirm(event, 'handwritten')}
            className="rounded-md px-2.5 py-1 text-[11.5px] disabled:opacity-50"
            style={{ background: 'var(--bg)', color: 'var(--fg)', border: '1px solid var(--border)' }}
          >
            Handwritten
          </button>
        )}
        {confirmationPending && <span role="status" className="self-center text-[11px]" style={{ color: 'var(--muted)' }}>Saving choice…</span>}
      </div>
    </div>
  );
}
