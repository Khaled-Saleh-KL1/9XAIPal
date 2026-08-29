import { useEffect, useRef, useState } from 'react';

/**
 * Collects a URL for the third ingestion pipeline (web article import) and
 * hands it up once validated — App owns actually calling importArticleUrl,
 * the same "modal decides what, App decides how" split UploadKindModal uses.
 */
export function ImportUrlModal({
  onImport,
  onCancel,
}: {
  onImport: (url: string) => void;
  onCancel: () => void;
}) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = () => {
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Paste a link first.');
      return;
    }
    if (!/^https?:\/\//i.test(trimmed)) {
      setError('Only http:// and https:// links can be imported.');
      return;
    }
    onImport(trimmed);
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center px-6"
      style={{ background: 'color-mix(in oklch, var(--bg), transparent 8%)', backdropFilter: 'blur(6px)' }}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-[560px] rounded-2xl overflow-hidden"
        style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-7 pt-7 pb-2">
          <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
            Import a web article
          </div>
          <div className="text-[12.5px] mt-1" style={{ color: 'var(--muted)' }}>
            Paste a link — it reads exactly like a paper, with margin notes, search, and the AI panel.
          </div>
        </div>
        <div className="px-7 py-5">
          <input
            ref={inputRef}
            value={url}
            onChange={(e) => { setUrl(e.target.value); setError(null); }}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
            placeholder="https://example.com/an-article"
            className="w-full px-3 py-2.5 rounded-md text-[13px]"
            style={{
              background: 'var(--bg-2)',
              border: `1px solid ${error ? '#ef4444' : 'var(--border)'}`,
              color: 'var(--fg)',
              outline: 'none',
            }}
          />
          {error && (
            <div className="text-[12px] mt-2" style={{ color: '#ef4444' }}>{error}</div>
          )}
        </div>
        <div className="px-7 py-3.5 flex items-center gap-3" style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}>
          <button
            onClick={onCancel}
            className="text-[12px] px-3 py-1.5 rounded-md"
            style={{ color: 'var(--muted)', border: '1px solid var(--border)', background: 'var(--bg)' }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            className="ml-auto text-[12.5px] px-3 py-1.5 rounded-md"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}
