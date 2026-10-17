const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const EMAIL = /^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/;
const ALLOWED = new Set(['http:', 'https:', 'mailto:']);

/** What the link dialog stores for what was typed, or null to refuse it. A
 *  bare address gets https://, a bare email mailto:; anything that is not
 *  web or mail is refused, `javascript:` included. */
export function normalizeHref(typed: string): string | null {
  const value = typed.trim();
  if (!value) return null;
  const candidate = SCHEME.test(value)
    ? value
    : EMAIL.test(value)
      ? `mailto:${value}`
      : `https://${value}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return null;
  }
  if (!ALLOWED.has(url.protocol)) return null;
  if (url.protocol === 'mailto:') return EMAIL.test(url.pathname) ? url.href : null;
  // A host with no dot is almost always a typo, not an intranet name.
  if (!url.hostname.includes('.') && url.hostname !== 'localhost') return null;
  return url.href;
}

/** The text a link inserted with nothing selected shows. */
export function linkText(href: string): string {
  return href.startsWith('mailto:') ? href.slice('mailto:'.length) : href;
}
