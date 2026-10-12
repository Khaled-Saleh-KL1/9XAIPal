# Public discovery: SEO and AI search

The public site identifies 9XAIPal as Khaled Saleh's personal AI research and
reading companion, and identifies Khaled as a 9XAI fellow at Al Hussein
Technical University (HTU). The program description is grounded in
[HTU's published explanation](https://htu.edu.jo/news/al-balbeisi-and-al-hinti-sign-mou-to-advance-the-use-of-artificial-intelligence-in-the-public-sector).

## Where to edit

- `frontend/src/landing/discovery.ts`: public facts, FAQs and JSON-LD entity graph.
- `frontend/src/landing/content.ts`: landing-page biography and links.
- `frontend/build/discovery.ts`: Vite plugin generating metadata, initial HTML,
  `about.html`, `robots.txt`, `sitemap.xml` and supplementary `llms.txt`.

The build puts metadata and readable public descriptions into the initial
homepage HTML. React replaces that content when it mounts. `/about.html` is a
complete static page, linked from the landing footer, that works without
JavaScript or signing in. JSON-LD uses stable identifiers shared with Khaled's
portfolio at `https://portfolio.kl1.site/` for the person, program and product.
Keep the portfolio's descriptions and identifiers consistent when updating
these public facts.

Only the homepage and About page belong in the sitemap. API routes, uploaded
document assets and API documentation are excluded by `robots.txt`. Robots
rules do not provide access control; existing authentication still controls
private data. `llms.txt` is a supplementary reference, not a requirement or a
guarantee of inclusion in AI answers.

## Verification

Run `npm run build` from `frontend/` and check the generated `dist/index.html`
and `dist/about.html` for canonical URLs, a parseable JSON-LD graph and visible
profile/product descriptions. Run the landing-page tests after changing its
biography or navigation. After deployment, verify that `/robots.txt` returns
plain text, `/sitemap.xml` returns XML, and `/about.html` serves the static
page rather than the SPA fallback.

Search and AI systems decide when to crawl, index and cite the pages. Website
changes make the facts publicly discoverable; they do not update every model's
stored knowledge. Site owners can submit each sitemap through their search
console account to request discovery.
