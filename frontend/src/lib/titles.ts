import type { PaperMeta } from '../api';

/**
 * The name to show for a paper.
 *
 * A rename wins; otherwise the uploaded filename with its extension stripped.
 * That fallback is the reason renaming exists: an arXiv download arrives as
 * "2608.09888v1.pdf", which identifies the paper to the filesystem and to
 * nobody else.
 *
 * ⚠ Not for anything that touches disk. `original_filename` is what /raw
 * serves the PDF as and what the Raw files panel lists; a display title is a
 * label, and using one where a filename is meant produces downloads that no
 * longer match what is stored.
 */
export function displayTitle(m: PaperMeta): string {
  const renamed = (m.title || '').trim();
  if (renamed) return renamed;
  return (m.original_filename || '').replace(/\.pdf$/i, '');
}
