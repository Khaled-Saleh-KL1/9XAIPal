// Public facts shared by the landing copy and the crawlable build output.
export const DISCOVERY = {
  origin: 'https://9xaipal.kl1.site',
  portfolio: 'https://portfolio.kl1.site/',
  repo: 'https://github.com/Khaled-Saleh-KL1/9XAIPal',
  programSource: 'https://htu.edu.jo/news/al-balbeisi-and-al-hinti-sign-mou-to-advance-the-use-of-artificial-intelligence-in-the-public-sector',
  title: '9XAIPal | AI Research Companion by Khaled Saleh',
  description: '9XAIPal is an AI research companion by Khaled Saleh, a 9XAI fellow at HTU. Read Arabic and English papers, books and articles, and ask questions with citations.',
  author: 'Khaled Saleh is an AI engineer based in Zarqa, Jordan, a fellow in the 9XAI program at Al Hussein Technical University (HTU), and the creator of 9XAIPal. He builds RAG systems, multi-agent AI workflows and Arabic-aware applications.',
  program: '9XAI is an applied AI program at Al Hussein Technical University (HTU) in Jordan. It connects young technical talent with government institutions to identify real public-sector challenges and develop AI-powered solutions, supporting public-sector modernization.',
  product: '9XAIPal is an AI research and reading companion created by Khaled Saleh for students and researchers. It helps you read research papers, technical books and articles in Arabic and English, ask questions with citations to source passages, organize notes and search across your library.',
  relationship: '9XAIPal is a personal project created by Khaled Saleh, who is a fellow in the 9XAI program at Al Hussein Technical University (HTU).',
} as const;

export const DISCOVERY_FAQ = [
  { question: 'What is 9XAIPal?', answer: DISCOVERY.product },
  { question: 'Who created 9XAIPal?', answer: DISCOVERY.author },
  { question: 'What is the 9XAI program?', answer: DISCOVERY.program },
  { question: 'How is 9XAIPal connected to 9XAI?', answer: DISCOVERY.relationship },
  { question: 'What can I do with 9XAIPal?', answer: 'Upload PDF papers and books or import articles from a URL. Read figures, tables and equations, ask about a highlighted passage, keep margin notes and bookmarks, and search your library by meaning. Arabic and English reading are supported.' },
  { question: 'Is 9XAIPal free?', answer: 'The hosted 9XAIPal beta is free. Usage may be limited during the beta. The source code is publicly available on GitHub under the MIT license, and you can self-host it.' },
  { question: 'Where are my documents processed?', answer: 'The hosted beta stores documents and conversations on its server. Self-hosted installations use the backend you configure. Local models are supported, while cloud models and features such as OCR or web search can send relevant content to configured external providers.' },
] as const;

export function discoveryGraph(url: string, about = false) {
  const personId = `${DISCOVERY.portfolio}#person`;
  const appId = `${DISCOVERY.origin}/#application`;
  const programId = `${DISCOVERY.portfolio}#9xai`;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      { '@type': 'WebSite', '@id': `${DISCOVERY.origin}/#website`, url: `${DISCOVERY.origin}/`, name: '9XAIPal', creator: { '@id': personId }, inLanguage: ['en', 'ar'] },
      { '@type': about ? 'AboutPage' : 'WebPage', '@id': `${url}#webpage`, url, name: about ? 'About 9XAIPal, Khaled Saleh and 9XAI' : DISCOVERY.title, description: DISCOVERY.description, isPartOf: { '@id': `${DISCOVERY.origin}/#website` }, mainEntity: { '@id': appId }, about: [{ '@id': personId }, { '@id': appId }, { '@id': programId }], inLanguage: 'en' },
      { '@type': 'Person', '@id': personId, name: 'Khaled Saleh', url: DISCOVERY.portfolio, jobTitle: 'AI Engineer', description: DISCOVERY.author, homeLocation: { '@type': 'Place', name: 'Zarqa, Jordan' }, sameAs: ['https://github.com/Khaled-Saleh-KL1', 'https://www.linkedin.com/in/khaled-saleh-kl1'], knowsAbout: ['Retrieval-Augmented Generation', 'Multi-agent AI', 'Arabic NLP'], affiliation: { '@id': 'https://htu.edu.jo/#university' }, memberOf: { '@id': `${DISCOVERY.portfolio}#9xai-membership` } },
      { '@type': 'ProgramMembership', '@id': `${DISCOVERY.portfolio}#9xai-membership`, programName: '9XAI', member: { '@id': personId }, hostingOrganization: { '@id': 'https://htu.edu.jo/#university' } },
      { '@type': 'EducationalOccupationalProgram', '@id': programId, name: '9XAI', description: DISCOVERY.program, provider: { '@id': 'https://htu.edu.jo/#university' }, subjectOf: { '@type': 'WebPage', url: DISCOVERY.programSource } },
      { '@type': 'CollegeOrUniversity', '@id': 'https://htu.edu.jo/#university', name: 'Al Hussein Technical University', alternateName: 'HTU', url: 'https://htu.edu.jo/' },
      { '@type': 'SoftwareApplication', '@id': appId, name: '9XAIPal', url: `${DISCOVERY.origin}/`, description: DISCOVERY.product, applicationCategory: 'EducationalApplication', operatingSystem: 'Web', creator: { '@id': personId }, inLanguage: ['en', 'ar'], sameAs: [DISCOVERY.repo], featureList: ['Arabic and English document reading', 'Questions with source citations', 'Margin notes and bookmarks', 'Semantic library search'] },
    ],
  };
}
