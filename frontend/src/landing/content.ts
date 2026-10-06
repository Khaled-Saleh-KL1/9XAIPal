export const LINKS = {
  repo: 'https://github.com/Khaled-Saleh-KL1/9XAIPal',
  portfolio: 'https://portfolio.kl1.site',
} as const;

export const BETA_NOTE = 'Free during the beta. Usage may be limited while we grow.';
export const BETA_LABEL = 'BETA';

export const NAVIGATION = {
  home: '9XAIPal home',
  journey: 'The journey',
  builtBy: 'Built by',
  pageSectionsLabel: 'Page sections',
  footerLinksLabel: 'Footer links',
  github: '★ GitHub',
  signIn: 'Sign in',
  openLibrary: 'Open your library',
} as const;

export const HERO = {
  eyebrow: 'FREE BETA · ARABIC + ENGLISH',
  titleBefore: 'Read deeper. ',
  titleAccent: 'Ask',
  titleAfter: ' your library.',
  subtitle:
    'Upload papers, books and articles in English or Arabic. Read them beautifully, ask questions, and get answers with citations to the exact page.',
  primaryCta: 'Try the free beta',
  secondaryCta: 'View the code',
  introDescription:
    'A drawn reader is buried under a storm of papers until the 9XAIPal mark sweeps them onto a shelf and hands over the one page they need.',
  caption: 'Made for reading across languages',
  artCaption: 'From a pile of papers to the one page you need.',
} as const;

export const SCENE_COPY = {
  pileKicker: 'A busy desk, made readable',
  pileDocs: ['Lab worksheet.pdf', 'Research notes · week 3', 'A reading list.pdf', 'Conference paper.pdf'],
  pileMedia: ['PDF', 'NOTES', 'SCAN', 'PDF', 'PDF', 'BOOK', 'PDF'],
  pileShelfStudent: 'Study shelf',
  pileShelfResearcher: 'Paper shelf',
  readFigure: 'Figure 2 · model performance by epoch',
  readFigureLabel: 'A line chart in an article',
  readArabic: 'تتعلّم النماذج من الأمثلة، ثم تتحسّن مع كل خطوة.',
  readEquationLabel: 'A training equation',
  readStudentPage: '14',
  readResearcherPage: '09',
  readStudentSideNote: 'slide 14',
  readResearcherSideNote: 'figure 2',
  askLabel: 'Ask your library',
  answerLabel: 'From your library',
  sourcesLabel: 'Sources',
  sourceLabel: 'Source passage',
  collectKicker: 'Saved to your desk',
  collectOtherNotes: ['Compare the two methods'],
  collectPersonaNote: {
    student: 'Check this before the exam',
    researcher: 'Check this before citing',
  },
  findLabel: 'Search the whole shelf',
  findExtraDoc: 'Reading list · week 4',
  findStudentPageLabels: ['p. 14', 'p. 204', 'p. 9', 'p. 4'],
  findResearcherPageLabels: ['p. 4', 'p. 23', 'p. 9', 'p. 4'],
  findNoPage: '···',
  findPdfLabel: 'PDF',
  findArabicLabel: 'AR',
  findShortcut: '⌘ K',
  findStatus: 'Matching pages from across your library',
} as const;

export type Persona = 'student' | 'researcher';

/** Example content per persona; the chapters' layout and motion never change. */
export const PERSONAS = {
  student: {
    label: "I'm a student",
    docs: ['Lecture 7 · Neural networks.pdf', 'Deep Learning, ch. 6', 'امتحان سابق ٢٠٢٥.pdf'],
    question: 'Explain backpropagation simply, with the slide it comes from.',
    answer: 'Backpropagation sends the error backwards through the network, layer by layer, to work out how much each weight should change.',
    citations: ['Lecture 7, slide 14', 'Deep Learning, p. 204'],
    sourcePage: '14',
    note: 'Backprop = chain rule, applied layer by layer',
    search: 'gradient',
    goal: 'Exam ready.',
  },
  researcher: {
    label: "I'm a researcher",
    docs: ['Attention Is All You Need.pdf', 'QIMMA: Arabic LLM leaderboard.pdf', 'التعرف الضوئي على الحروف العربية.pdf'],
    question: 'Which of these papers report results on Arabic OCR, and how do they compare?',
    answer: 'Two of the three do. The Arabic OCR survey reports the strongest character accuracy on printed text, while QIMMA evaluates Arabic language models rather than OCR.',
    citations: ['OCR survey, p. 9', 'QIMMA, p. 23'],
    sourcePage: '09',
    note: 'Arabic OCR baselines: survey p. 9, compare with our results',
    search: 'Arabic OCR',
    goal: 'Literature review ready.',
  },
} as const;

/** The journey's chapters, in order. Each capability appears in at least one. */
export const CHAPTERS = [
  { key: 'pile', label: 'Chapter 1 · The pile', title: 'Too many PDFs, two languages, one deadline.', body: 'Drop in papers, books, scans and articles, in English or Arabic. They land on your shelf, ready.' },
  { key: 'read', label: 'Chapter 2 · Read', title: 'Every page, readable.', body: 'Figures, tables and equations stay intact. Arabic flows right to left, the way it should.' },
  { key: 'ask', label: 'Chapter 3 · Ask', title: 'Ask anything. See where the answer came from.', body: 'Answers stream in as they are written, with citations that take you to the exact passage.' },
  { key: 'collect', label: 'Chapter 4 · Collect', title: 'Keep what matters.', body: 'Turn answers into sticky notes and build a study across documents on your desk.' },
  { key: 'find', label: 'Chapter 5 · Find again', title: 'Find the idea again, weeks later.', body: 'Search your whole library by meaning, in either language.' },
] as const;

export const JOURNEY = {
  eyebrow: 'Follow the journey',
  finaleTitle: 'Your turn.',
  progressLabel: 'Journey progress',
  chooseLabel: 'Choose your journey',
  personaPrompt: 'A reading day, your way',
} as const;

export const BUILT_BY_TITLE = 'Built by';
export const LINK_LABELS = {
  portfolio: 'Portfolio ↗',
  repo: 'GitHub repo ↗',
  footerPortfolio: 'Portfolio',
  footerRepo: 'GitHub',
} as const;
export const LANDING_COPY = {
  builtByAside: ['A personal project', 'for deeper reading.'],
} as const;

export const AUTHOR = {
  name: 'Khaled Saleh',
  monogram: 'KS',
  role: 'AI Engineer',
  bio: [
    'I design and ship end-to-end intelligent systems, from Arabic-aware NLP pipelines to RAG and multi-agent orchestration, using FastAPI, LangGraph and Gemini.',
    'I build production AI platforms at 9XAI / HTU. 9XAIPal is my own project: a reading companion I wanted for myself, now open as a free beta.',
  ],
} as const;

export const FOOTER = { copyright: '© 2026 Khaled Saleh', tagline: 'Made with care in Amman.' } as const;
