import type { Plugin } from 'vite';
import { DISCOVERY, DISCOVERY_FAQ, discoveryGraph } from '../src/landing/discovery';

const escapeHtml = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const jsonLd = (url: string, about = false) => JSON.stringify(discoveryGraph(url, about)).replaceAll('<', '\\u003c');
const aboutUrl = `${DISCOVERY.origin}/about.html`;
const aboutTitle = 'About 9XAIPal, Khaled Saleh and 9XAI';

function head(url: string, title: string, about = false) {
  return `<title>${escapeHtml(title)}</title>
<meta name="description" content="${escapeHtml(DISCOVERY.description)}">
<meta name="author" content="Khaled Saleh">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="${url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="9XAIPal">
<meta property="og:title" content="${escapeHtml(title)}">
<meta property="og:description" content="${escapeHtml(DISCOVERY.description)}">
<meta property="og:url" content="${url}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="${escapeHtml(title)}">
<meta name="twitter:description" content="${escapeHtml(DISCOVERY.description)}">
<script type="application/ld+json">${jsonLd(url, about)}</script>`;
}

function publicContent(about = false) {
  return `<main class="discovery-content" style="height:100%;overflow:auto;padding:clamp(24px,6vw,72px);box-sizing:border-box">
<div style="max-width:760px;margin:auto">
<nav aria-label="About navigation"><a href="/">9XAIPal</a> · <a href="${DISCOVERY.portfolio}">Khaled Saleh’s portfolio</a> · <a href="${DISCOVERY.repo}">GitHub</a></nav>
<h1>${about ? aboutTitle : '9XAIPal — AI research companion'}</h1>
<p>${escapeHtml(DISCOVERY.product)}</p>
<p><a href="/#/welcome">Try the free beta</a> · <a href="/about.html">About 9XAIPal and its creator</a></p>
${(about ? DISCOVERY_FAQ : DISCOVERY_FAQ.slice(1, 4)).map(({ question, answer }) => `<section><h2>${escapeHtml(question)}</h2><p>${escapeHtml(answer)}</p>${question === 'What is the 9XAI program?' ? `<p><a href="${DISCOVERY.programSource}">Read HTU’s description of the 9XAI program</a></p>` : ''}</section>`).join('\n')}
<footer><p>© 2026 Khaled Saleh</p></footer>
</div></main>`;
}

function publicAssets(): Record<string, string> {
  return {
    'about.html': `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
${head(aboutUrl, aboutTitle, true)}
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>body{margin:0;background:#fcfaf6;color:#302821;font:17px/1.7 system-ui,sans-serif}h1,h2{font-family:Georgia,serif;font-weight:400;line-height:1.2}h1{font-size:clamp(32px,5vw,48px)}h2{font-size:26px}section{padding:14px 0;border-top:1px solid #e5ded5}a{color:#994526;text-underline-offset:4px}nav,footer{font-size:14px}footer{margin-top:32px}@media(prefers-color-scheme:dark){body{background:#151210;color:#f2ebe2}a{color:#edab85}section{border-color:#40362d}}</style>
</head><body>${publicContent(true)}</body></html>`,
    'robots.txt': `User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /static/\nDisallow: /docs\nDisallow: /redoc\nDisallow: /openapi.json\nDisallow: /offline.html\n\nSitemap: ${DISCOVERY.origin}/sitemap.xml\n`,
    'sitemap.xml': `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>${DISCOVERY.origin}/</loc></url><url><loc>${aboutUrl}</loc></url></urlset>\n`,
    // Supplementary plain-text reference, not a substitute for indexable HTML.
    'llms.txt': `# 9XAIPal\n\n> ${DISCOVERY.product}\n\n## Creator\n\n${DISCOVERY.author}\n\n## 9XAI program\n\n${DISCOVERY.program}\n\n${DISCOVERY.relationship}\n\n## Public sources\n\n- [About 9XAIPal](${aboutUrl}): Product, creator, capabilities and FAQ.\n- [Khaled Saleh](${DISCOVERY.portfolio}): Official personal portfolio.\n- [Source code](${DISCOVERY.repo}): MIT-licensed repository and self-hosting documentation.\n- [9XAI at HTU](${DISCOVERY.programSource}): HTU’s published program description.\n`,
  };
}

export function discoveryPages(): Plugin {
  const assets = publicAssets();
  return {
    name: 'public-discovery',
    transformIndexHtml(html) {
      // Ordinary initial HTML for every visitor; React replaces it on mount.
      return html.replace('<title>9XAIPal</title>', head(`${DISCOVERY.origin}/`, DISCOVERY.title))
        .replace('<div id="root"></div>', `<div id="root">${publicContent()}</div>`);
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = req.url?.split('?')[0].slice(1) ?? '';
        if (!Object.hasOwn(assets, name) || !['GET', 'HEAD'].includes(req.method ?? '')) return next();
        res.setHeader('Content-Type', name.endsWith('.html') ? 'text/html; charset=utf-8' : name.endsWith('.xml') ? 'application/xml; charset=utf-8' : 'text/plain; charset=utf-8');
        res.end(req.method === 'HEAD' ? '' : assets[name]);
      });
    },
    generateBundle() {
      for (const [fileName, source] of Object.entries(assets)) {
        this.emitFile({ type: 'asset', fileName, source });
      }
    },
  };
}
