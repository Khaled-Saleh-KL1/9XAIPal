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
import { createElement } from 'react';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';
import type { Components } from 'react-markdown';

const SANITIZE_SCHEMA: typeof defaultSchema = {
  ...defaultSchema,
  // <video> is not in the GitHub-style default allowlist, so a video an
  // article actually contains would be stripped back out here after
  // rehype-raw parsed it (see services/article_extraction.py, which
  // recovers videos trafilatura drops and emits them as raw HTML — the
  // same path MinerU's tables already take). Only the playback attributes
  // are allowed through: no event handlers, and no `autoplay`, so nothing
  // in a reading view ever starts moving or making noise on its own.
  //
  // This is the same trust posture images already have here: the URL is
  // hotlinked straight from the source site rather than proxied (see
  // article_extraction.py's module docstring on that deliberate choice),
  // so a <video> is one more remote fetch from a site the reader has
  // already chosen to read — not a new class of exposure.
  tagNames: [...(defaultSchema.tagNames ?? []), 'video', 'source'],
  attributes: {
    ...defaultSchema.attributes,
    video: ['controls', 'poster', 'preload', 'src', 'width', 'height', 'playsInline', 'muted', 'loop'],
    source: ['src', 'type'],
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
  // rehype-sanitize drops any URL whose protocol isn't allowed for that
  // attribute; without src listed here a <video src="https://..."> would
  // survive as a tag with no source at all.
  protocols: {
    ...defaultSchema.protocols,
    src: ['http', 'https'],
  },
};

export const MARKDOWN_REMARK: PluggableList = [remarkGfm, remarkMath];
export const MARKDOWN_REHYPE: PluggableList = [
  rehypeRaw,
  [rehypeSanitize, SANITIZE_SCHEMA],
  [rehypeKatex, { errorColor: 'var(--muted)' }],
];

/**
 * A link in extracted content — most commonly an article's own hyperlinks,
 * kept as real markdown links since services/article_extraction.py started
 * passing include_links=True to trafilatura — should open in a new tab, not
 * navigate the reader's SPA away. react-markdown's default <a> has no
 * target/rel at all; spread this into any ReactMarkdown's `components` prop.
 */
export const MARKDOWN_LINK_COMPONENT: Pick<Components, 'a'> = {
  a: ({ href, children, ...rest }) =>
    createElement('a', { href, target: '_blank', rel: 'noopener noreferrer', ...rest }, children),
};
