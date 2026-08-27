import { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';

/** Bare button + dropdown, meant to sit inline as the trailing item in a view's own header row. */
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

/**
 * Floating top-right corner control, for views with no header of their own to
 * embed UserMenuInline into.
 *
 * ⚠ Views that DO have their own header (LibraryView) embed UserMenuInline
 * directly instead of using this. A viewport-edge-fixed control can visually
 * land on top of a header's own right-aligned buttons: a header's content is
 * usually capped at a max-width and centered, so on a normal (non-ultrawide)
 * window its right edge sits close to the actual screen edge — close enough
 * for this fixed badge to overlap it. Was actually happening over
 * LibraryView's "Raw files" button before this split.
 */
export function UserMenu() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="fixed top-3 right-3 z-30">
      <UserMenuInline />
    </div>
  );
}
