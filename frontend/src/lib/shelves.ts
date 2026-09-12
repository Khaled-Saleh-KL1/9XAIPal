/**
 * Shelves: how the library, the Desk rail and the study picker all group
 * documents the same way — by kind (Books / Research / Articles), with
 * everything the reader has marked "done reading" on its own shelf, and the
 * Done shelf sub-divided into the reader's own folders.
 *
 * One definition, three call sites, so "Done" cannot mean something slightly
 * different in the picker than it does in the library.
 */

export type ShelfKey = 'book' | 'paper' | 'article' | 'done';

export const SHELVES: { key: ShelfKey; label: string }[] = [
  { key: 'book', label: 'Books' },
  { key: 'paper', label: 'Research' },
  { key: 'article', label: 'Articles' },
  { key: 'done', label: 'Done' },
];

export const SHELF_LABEL: Record<ShelfKey, string> = Object.fromEntries(
  SHELVES.map((s) => [s.key, s.label]),
) as Record<ShelfKey, string>;

/** The fields grouping needs, in either the API (`PaperMeta`) or the UI
 *  (`Paper`) spelling — pass whichever the caller has. */
export interface Shelvable {
  doc_kind?: string | null;
  docKind?: string | null;
  done_at?: string | null;
  doneAt?: string | null;
  done_folder?: string | null;
  doneFolder?: string | null;
}

export function isDone(x: Shelvable): boolean {
  return Boolean(x.done_at ?? x.doneAt);
}

export function doneFolderOf(x: Shelvable): string | null {
  return isDone(x) ? (x.done_folder ?? x.doneFolder ?? null) : null;
}

/** Older rows have no doc_kind; they are papers by schema default. */
export function kindOf(x: Shelvable): 'book' | 'paper' | 'article' {
  const k = x.doc_kind ?? x.docKind;
  return k === 'book' || k === 'article' ? k : 'paper';
}

export function shelfOf(x: Shelvable): ShelfKey {
  return isDone(x) ? 'done' : kindOf(x);
}

export interface ShelfGroup<T> {
  key: ShelfKey;
  label: string;
  /** Items directly on this shelf (for `done`: the ones in no folder). */
  items: T[];
  /** `done` only: the reader's folders, each with its items. */
  folders: { name: string; items: T[] }[];
  /** items + everything inside folders. */
  total: number;
}

/**
 * Group into the four shelves, in SHELVES order, dropping empty ones. Input
 * order is preserved within each group, so a caller that sorted first keeps
 * its sort. Folder names sort alphabetically — they are the reader's own
 * labels, and "where is Technical Books" should not depend on which paper
 * was finished first.
 */
export function groupByShelf<T extends Shelvable>(items: T[]): ShelfGroup<T>[] {
  const buckets = new Map<ShelfKey, T[]>();
  const folders = new Map<string, T[]>();
  for (const it of items) {
    const shelf = shelfOf(it);
    const folder = doneFolderOf(it);
    if (shelf === 'done' && folder) {
      const arr = folders.get(folder) ?? [];
      arr.push(it);
      folders.set(folder, arr);
    } else {
      const arr = buckets.get(shelf) ?? [];
      arr.push(it);
      buckets.set(shelf, arr);
    }
  }
  const out: ShelfGroup<T>[] = [];
  for (const { key, label } of SHELVES) {
    const direct = buckets.get(key) ?? [];
    const fs =
      key === 'done'
        ? [...folders.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([name, xs]) => ({ name, items: xs }))
        : [];
    const total = direct.length + fs.reduce((n, f) => n + f.items.length, 0);
    if (total === 0) continue;
    out.push({ key, label, items: direct, folders: fs, total });
  }
  return out;
}

/** Every folder name in use, alphabetical — what the "move to folder" panel lists. */
export function doneFolders(items: Shelvable[]): string[] {
  const names = new Set<string>();
  for (const it of items) {
    const f = doneFolderOf(it);
    if (f) names.add(f);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}
