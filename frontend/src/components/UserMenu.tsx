import { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';

/**
 * User badge + logout dropdown. Meant to sit inline as the trailing item in
 * a view's own header row — every screen has its own header, and each one
 * embeds this directly rather than a global corner overlay.
 *
 * ⚠ There used to also be a `UserMenu` wrapper that pinned this to a fixed
 * viewport corner (`position: fixed; top; right`), for routes with no header
 * of their own. It was removed once every route turned out to have one: a
 * viewport-edge-fixed control silently lands on top of whatever a header's
 * own rightmost button is — a header's content is usually capped at a
 * max-width and centered, so on a normal (non-ultrawide) window its right
 * edge sits close to the actual screen edge. This bit twice — once over
 * LibraryView's "Raw files" button, then again over PdfViewer's "Read
 * structured" button — before every view got its own inline copy instead.
 */
export function UserMenuInline() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  if (!user) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[12px] px-3 py-1.5 rounded-full"
        style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--fg-2)' }}
      >
        {user.display_name || user.email}
      </button>
      {open && (
        <div
          className="mt-1 rounded-lg overflow-hidden absolute right-0 z-30"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 8px 24px -8px rgba(0,0,0,0.18)' }}
        >
          <button
            onClick={() => { setOpen(false); void logout(); }}
            className="text-[12px] px-4 py-2 whitespace-nowrap w-full text-left"
            style={{ color: 'var(--fg)' }}
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
