import { useState, type ReactNode } from 'react';
import { groupByShelf, type Shelvable, type ShelfKey } from '../lib/shelves';

/**
 * A list of documents shown as collapsible shelves — Books, Research,
 * Articles, Done — with the Done shelf's folders nested one level in, the
 * way a file manager shows a tree. Used by the Desk rail and by the study
 * picker; the caller renders each row, this only owns the grouping and the
 * open/closed state.
 *
 * Every shelf starts open: the reader asked to be able to *minimise* what is
 * not in play right now, not to hunt for what is. A shelf's state is keyed by
 * `<shelf>` and a folder's by `<shelf>/<folder>`, held here per mount — the
 * rail and the picker are separate trees and remembering one in the other
 * would be surprising.
 */
export function ShelfGroups<T extends Shelvable & { id: string }>({
  items,
  renderItem,
  renderHead,
  className = '',
}: {
  items: T[];
  renderItem: (item: T) => ReactNode;
  /** Optional extra content on a shelf/folder header row (an "add all"). */
  renderHead?: (group: { key: ShelfKey; folder: string | null; items: T[] }) => ReactNode;
  className?: string;
}) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const groups = groupByShelf(items);

  const head = (k: string, label: string, count: number, depth: 0 | 1, extra?: ReactNode) => {
    const open = !closed.has(k);
    return (
      <div className={`shelf-head${depth ? ' is-folder' : ''}`}>
        <button
          type="button"
          className="shelf-toggle"
          onClick={() => toggle(k)}
          aria-expanded={open}
          title={open ? `Collapse ${label}` : `Expand ${label}`}
        >
          <span className={`shelf-caret${open ? ' is-open' : ''}`} aria-hidden="true">▸</span>
          {depth === 1 && <span className="shelf-folder-glyph" aria-hidden="true">▤</span>}
          <span className="shelf-label">{label}</span>
          <span className="shelf-count">{count}</span>
        </button>
        {extra}
      </div>
    );
  };

  return (
    <div className={`shelf-groups ${className}`.trim()}>
      {groups.map((g) => {
        const k = g.key;
        const open = !closed.has(k);
        return (
          <section key={k} className={`shelf${open ? '' : ' is-closed'}`}>
            {head(k, g.label, g.total, 0, renderHead?.({ key: g.key, folder: null, items: [...g.items, ...g.folders.flatMap((f) => f.items)] }))}
            {open && (
              <div className="shelf-body">
                {g.items.map((it) => <div key={it.id} className="shelf-item">{renderItem(it)}</div>)}
                {g.folders.map((f) => {
                  const fk = `${k}/${f.name}`;
                  const fopen = !closed.has(fk);
                  return (
                    <section key={fk} className={`shelf-folder${fopen ? '' : ' is-closed'}`}>
                      {head(fk, f.name, f.items.length, 1, renderHead?.({ key: g.key, folder: f.name, items: f.items }))}
                      {fopen && (
                        <div className="shelf-body is-folder">
                          {f.items.map((it) => <div key={it.id} className="shelf-item">{renderItem(it)}</div>)}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
