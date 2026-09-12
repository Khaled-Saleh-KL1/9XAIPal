import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '../contexts/AuthContext';

/**
 * User badge + logout dropdown. Meant to sit inline as the trailing item in
 * a view's own header row: every screen has its own header, and each one
 * embeds this directly rather than a global corner overlay.
 *
 * ⚠ There used to also be a `UserMenu` wrapper that pinned this to a fixed
 * viewport corner (`position: fixed; top; right`), for routes with no header
 * of their own. It was removed once every route turned out to have one: a
 * viewport-edge-fixed control silently lands on top of whatever a header's
 * own rightmost button is, since a header's content is usually capped at a
 * max-width and centered, so on a normal (non-ultrawide) window its right
 * edge sits close to the actual screen edge. This bit twice: once over
 * LibraryView's "Raw files" button, then again over PdfViewer's "Read
 * structured" button, before every view got its own inline copy instead.
 */
export function UserMenuInline() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const nameRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  // ⚠ The menu is rendered into document.body at a fixed position, not as a
  // child of the badge. The badge lives inside each view's header, and the
  // library's header row is `overflow-x: auto` so it can swipe on a phone —
  // and an overflow-x that is not `visible` clips overflow-y too. A dropdown
  // hanging below the badge was drawn but cut off at the header's edge:
  // invisible, unclickable, and the reason "I can't sign out" was true on
  // the library and false on the Desk. Nothing inside a scroller can escape
  // it; rendering outside is the only fix that survives the next header.
  const toggle = () => {
    if (!open && nameRef.current) {
      const r = nameRef.current.getBoundingClientRect();
      setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    }
    setOpen((v) => !v);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (nameRef.current?.contains(t) || popRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    // A fixed-position menu does not follow the badge if the page scrolls or
    // the window resizes; closing is simpler than tracking, and the menu is
    // one button.
    const onMove = () => setOpen(false);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onMove);
    window.addEventListener('scroll', onMove, true);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onMove);
      window.removeEventListener('scroll', onMove, true);
    };
  }, [open]);

  if (!user) return null;

  return (
    <>
      <button
        ref={nameRef}
        type="button"
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        className="text-[12px] px-3 py-1.5 rounded-full"
        style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--fg-2)' }}
      >
        {user.display_name || user.email}
      </button>
      {open && pos && createPortal(
        <div ref={popRef} className="user-menu-pop" role="menu" style={{ top: pos.top, right: pos.right }}>
          <button
            type="button"
            role="menuitem"
            className="user-menu-signout"
            onClick={() => { setOpen(false); void logout(); }}
          >
            Sign out
          </button>
        </div>,
        document.body,
      )}
    </>
  );
}
