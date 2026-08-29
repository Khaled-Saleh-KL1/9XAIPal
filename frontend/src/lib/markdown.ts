/**
 * Shared markdown pipeline for everything that renders model output or
 * extracted paper content (ArticleBlock, NoteCard, BookReadingView, ChatPane).
 *
 * rehype-raw is needed because MinerU emits raw <table> HTML and models
 * occasionally emit inline HTML, but raw HTML without sanitization is an XSS
 * vector (a jailbroken model answer or hostile web-research snippet could
 * inject <script>/<iframe>/onerror handlers). rehype-sanitize runs after raw
 * parsing and strips everything outside the GitHub-style allowlist; the only
 * extensions are the math classes KaTeX needs (KaTeX itself runs after
 * sanitization, so its generated spans are unaffected).
 *
 * errorColor: formula extraction (MinerU's OCR, or a model transcribing a
 * paper's equations) occasionally produces LaTeX KaTeX cannot parse — a
 * genuinely malformed source, not a bug we can fix by retrying. KaTeX's own
 * default then renders the raw TeX source in alarming red (#cc0000); this
 * points it at the app's muted-text color instead (see .katex-error in
 * index.css for the rest of the calmer, code-like treatment).
 */
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';

const SANITIZE_SCHEMA: typeof defaultSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code ?? []),
      ['className', 'language-math', 'math-inline', 'math-display'],
    ],
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      ['className', 'math', 'math-inline'],
    ],
    div: [
      ...(defaultSchema.attributes?.div ?? []),
      ['className', 'math', 'math-display'],
    ],
  },
};

export const MARKDOWN_REMARK: PluggableList = [remarkGfm, remarkMath];
export const MARKDOWN_REHYPE: PluggableList = [
  rehypeRaw,
  [rehypeSanitize, SANITIZE_SCHEMA],
  [rehypeKatex, { errorColor: 'var(--muted)' }],
];
