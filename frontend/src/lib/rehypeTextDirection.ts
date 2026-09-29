/**
 * Obsidian-style direction for rendered markdown: every block-level element
 * flows in the direction of its own text, so an Arabic paragraph reads
 * right-to-left and a neighbouring English one stays left-to-right, whatever
 * the page's base direction is.
 *
 * Runs last in MARKDOWN_REHYPE, after sanitization and KaTeX, so it only sees
 * final elements and the `dir` attribute is never stripped. Code and formulas
 * do not vote: a `code` span, a `pre` block or KaTeX output is Latin by nature
 * and would otherwise drag an Arabic sentence that merely mentions a variable
 * name towards LTR. An element with no letters left after that (a paragraph
 * that is just a formula) gets no `dir` and inherits its surroundings.
 */
import type { Element, Nodes, Root } from 'hast';
import { visit } from 'unist-util-visit';
import { textDirection } from './documentDirection';

const DIRECTED_TAGS = new Set([
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote',
  'td', 'th', 'ul', 'ol', 'table', 'dd', 'dt', 'figcaption',
]);

const NON_VOTING_TAGS = new Set(['pre', 'code']);

function isNonVoting(node: Element): boolean {
  if (NON_VOTING_TAGS.has(node.tagName)) return true;
  const cls = node.properties?.className;
  const text = Array.isArray(cls) ? cls.join(' ') : typeof cls === 'string' ? cls : '';
  return text.includes('katex') || text.includes('math');
}

function votingText(node: Nodes): string {
  if (node.type === 'text') return node.value;
  if (node.type === 'element' && isNonVoting(node)) return '';
  if (node.type === 'element' || node.type === 'root') {
    let out = '';
    for (const child of node.children) out += votingText(child);
    return out;
  }
  return '';
}

export function rehypeTextDirection() {
  return (tree: Root) => {
    visit(tree, 'element', (node: Element) => {
      if (!DIRECTED_TAGS.has(node.tagName)) return;
      const direction = textDirection(votingText(node));
      if (direction) node.properties = { ...node.properties, dir: direction };
    });
  };
}
