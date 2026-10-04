import { FOOTER, LINK_LABELS, LINKS, NAVIGATION } from '../../landing/content';

export function Footer() {
  return (
    <footer className="landing-footer">
      <p>{FOOTER.copyright}</p>
      <p>{FOOTER.tagline}</p>
      <nav aria-label={NAVIGATION.footerLinksLabel}>
        <a href={LINKS.portfolio} target="_blank" rel="noopener noreferrer">{LINK_LABELS.footerPortfolio}</a>
        <a href={LINKS.repo} target="_blank" rel="noopener noreferrer">{LINK_LABELS.footerRepo}</a>
      </nav>
    </footer>
  );
}
