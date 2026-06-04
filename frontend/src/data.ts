import type { Paper, Chunk, ChatMessage, ProcessingStep } from './types';

// ── Sample library ────────────────────────────────────────────────────────────

export const LIBRARY: Paper[] = [
  {
    id: 'p1',
    title: 'Structural Chunking for Long-Form Document Understanding',
    authors: 'Aiyer, Nakamura, Vázquez',
    venue: 'ACL 2025',
    pages: 14,
    added: 'May 18',
    progress: 0.42,
    tags: ['NLP', 'retrieval'],
    pinned: true,
  },
  {
    id: 'p2',
    title: 'Local-First Software: Reclaiming Data Ownership Without the Cloud',
    authors: 'Kleppmann, Wiggins, van Hardenberg',
    venue: "Onward! 2019",
    pages: 31,
    added: 'May 16',
    progress: 1.0,
    tags: ['systems', 'p2p'],
  },
  {
    id: 'p3',
    title: 'Vision-Language Pretraining for Scientific Figure Grounding',
    authors: 'Park, Okonkwo, Liu',
    venue: 'NeurIPS 2024',
    pages: 22,
    added: 'May 14',
    progress: 0.18,
    tags: ['VLM', 'multimodal'],
  },
  {
    id: 'p4',
    title: 'Retrieval-Augmented Mathematical Reasoning at Inference Time',
    authors: 'Singh, Hofmann, Pereira',
    venue: 'ICLR 2025',
    pages: 18,
    added: 'May 11',
    progress: 0.66,
    tags: ['math', 'RAG'],
  },
  {
    id: 'p5',
    title: 'A Practical Survey of Embedding Compression for On-Device Retrieval',
    authors: 'Bauer, Yamada',
    venue: 'TMLR 2024',
    pages: 47,
    added: 'May 09',
    progress: 0.0,
    tags: ['embeddings', 'edge'],
  },
  {
    id: 'p6',
    title: 'Sparse Mixture-of-Readers for Long Context',
    authors: 'Chen, Romero, El-Sayed, Ho',
    venue: 'Preprint',
    pages: 26,
    added: 'May 04',
    progress: 0.91,
    tags: ['LLM', 'long-ctx'],
  },
];

// ── Structural chunks for the reading demo ────────────────────────────────────

export const CHUNKS: Chunk[] = [
  {
    type: 'heading',
    level: 1,
    text: 'Structural Chunking for Long-Form Document Understanding',
    meta: 'Aiyer, Nakamura, Vázquez · ACL 2025',
  },
  { type: 'heading', level: 2, text: '3.2  Boundary detection on hierarchical PDFs' },
  {
    type: 'paragraph',
    text: 'Existing retrieval pipelines treat a research paper as a flat stream of tokens, sliding a fixed window across the document until the budget is exhausted. This is an unforced error: papers are deeply hierarchical artifacts, and the boundaries that matter to a reader — the end of a derivation, the caption beneath a figure, the closing sentence of a related-work paragraph — are nearly always recoverable from layout signals alone.',
  },
  {
    type: 'paragraph',
    text: 'We propose treating each structural unit as the atomic retrieval target. A chunk is whatever the layout says it is: one paragraph, one display equation, one figure with its caption. Empirically this turns out to be the right granularity for both reader-facing summarisation and downstream question answering.',
  },
  {
    type: 'math',
    label: '(7)',
    caption: 'Chunk-level retrieval probability under a temperature-scaled inner-product kernel.',
  },
  {
    type: 'paragraph',
    text: 'The temperature parameter τ is small (we use 0.07) which sharpens the posterior over chunks. In practice this means a well-posed question almost always resolves to one or two chunks, rather than blurring across a dozen weakly-related fragments.',
  },
  {
    type: 'figure',
    caption: 'Figure 4. Distribution of chunk lengths across 12,400 ACL papers, broken down by structural class. Display equations are nearly uniform around 32 tokens; paragraphs are heavy-tailed.',
    placeholder: 'fig-4 · chunk-length histogram',
  },
  {
    type: 'paragraph',
    text: 'Note the bimodality of the paragraph distribution. Introductions and related-work paragraphs cluster around 120 tokens, while the bodies of methods sections balloon to 280 — a sign that authors compress prose in the framing and elaborate in the technical core.',
  },
  {
    type: 'list',
    items: [
      'Boundaries are recovered from PDF layout, not learned end-to-end.',
      'Math and figures are first-class chunks, not noise to be stripped.',
      'Retrieval operates over structural units; reading does too.',
    ],
  },
  { type: 'heading', level: 2, text: '3.3  Why one chunk at a time' },
  {
    type: 'paragraph',
    text: 'Presenting a single chunk at a time forces the reader to commit. There is no scrolling past the dense equation, no skimming the figure caption. The interface obliges you to either understand the unit in front of you or admit that you don\'t — and in the second case, to ask.',
  },
];

// ── Sample chat history ───────────────────────────────────────────────────────

export const INITIAL_CHAT: ChatMessage[] = [
  { role: 'user', text: 'why does τ = 0.07 specifically?' },
  {
    role: 'assistant',
    text: 'The paper cites Wu et al. (2018) on contrastive learning, where τ = 0.07 was found to be a stable sweet spot for normalized inner-product similarities. It\'s low enough to sharpen the posterior over chunks but not so low that gradients collapse during the auxiliary training objective in §4.2.',
    refs: ['§3.2 eq. 7', '§4.2', 'Wu et al. 2018'],
  },
];

// ── Processing steps ──────────────────────────────────────────────────────────

export const PROCESSING_STEPS: ProcessingStep[] = [
  {
    id: 1,
    title: 'Extracting Structure & Math',
    sub: 'Parsing layout, lifting equations to LaTeX',
    detail: [
      '→ 42 pages · 318 paragraphs identified',
      '→ 27 display equations · 9 inline figures',
      '→ bibliography demarcated',
    ],
  },
  {
    id: 2,
    title: 'Enhancing Context via VLM',
    sub: 'Describing figures, grounding captions',
    detail: [
      '→ 9 figures captioned and linked',
      '→ 4 tables transcribed to markdown',
      '→ cross-references resolved (§4.2 → eq. 12)',
    ],
  },
  {
    id: 3,
    title: 'Building Local Vector Database',
    sub: 'Embedding chunks, persisting to ~/.9xaipal',
    detail: [
      '→ 354 structural chunks queued',
      '→ embed-en-v4 · 768-dim · int8',
      '→ writing to ~/.9xaipal/index.duckdb',
    ],
  },
];
