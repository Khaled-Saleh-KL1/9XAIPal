import { useState } from 'react';
import { downloadBibtex, downloadNotesMarkdownZip, downloadAnkiFlashcards, downloadLibraryCsv } from '../api';

/**
 * "Export" dropdown for the whole library — BibTeX, Markdown (Obsidian),
 * Anki flashcards, a library CSV. Same click-to-toggle dropdown shape as
 * UserMenuInline (components/UserMenu.tsx): a header-row button that opens
 * an absolutely-positioned panel underneath it, not a new route — this is a
 * one-shot action, not a page anyone navigates back to.
 *
 * Each item is a plain click handler that hands off to the browser's own
 * download machinery (see api.ts's downloadFrom) rather than anything this
 * component needs to track state for — there's no loading/progress state
 * here because there's no job to poll: every export is one fast, synchronous
 * request.
 */
export function ExportMenu() {
  const [open, setOpen] = useState(false);

  const items: { label: string; hint: string; onClick: () => void }[] = [
    { label: 'BibTeX', hint: 'Citable references (.bib)', onClick: downloadBibtex },
    { label: 'Markdown', hint: 'Notes, for Obsidian (.zip)', onClick: downloadNotesMarkdownZip },
    { label: 'Anki flashcards', hint: 'Q&A notes (.txt)', onClick: downloadAnkiFlashcards },
    { label: 'Library CSV', hint: 'Spreadsheet of your papers', onClick: downloadLibraryCsv },
  ];

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
        style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
        title="Export your library and notes"
      >
        <span style={{ color: 'var(--accent)', fontSize: 11 }}>⇩</span>
        Export
      </button>
      {open && (
        <>
          {/* Full-viewport click-catcher so choosing an item (or clicking
              anywhere else) closes the menu — same as UserMenuInline would
              need if it had more than one destructive action; here every
              item DOES need this since there's no second click to dismiss
              on, unlike a plain outside-click no-op. */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div
            className="mt-1 rounded-lg overflow-hidden absolute right-0 z-30"
            style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 8px 24px -8px rgba(0,0,0,0.18)', minWidth: 200 }}
          >
            {items.map((item) => (
              <button
                key={item.label}
                onClick={() => { setOpen(false); item.onClick(); }}
                className="text-[12.5px] px-4 py-2.5 whitespace-nowrap w-full text-left block"
                style={{ color: 'var(--fg)' }}
              >
                <div>{item.label}</div>
                <div className="text-[10.5px]" style={{ color: 'var(--faint)' }}>{item.hint}</div>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
