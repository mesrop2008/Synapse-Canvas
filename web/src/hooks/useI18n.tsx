import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  LOCALES,
  PLACEHOLDER,
  interpolate,
  isLocale,
  setActiveLocale,
  template,
  type Locale,
  type MessageKey,
  type MessageParams,
} from '../../i18n';

const STORAGE_KEY = 'synapse.locale';

interface I18nContextValue {
  locale: Locale;
  locales: readonly Locale[];
  setLocale: (next: Locale) => void;
  t: (key: MessageKey, params?: MessageParams) => string;
  /** For the few strings that wrap a value in markup. */
  tNode: (key: MessageKey, params: Record<string, ReactNode>) => ReactNode;
  /** "3 minutes ago" / "3 минуты назад" -- Intl owns the plural rules. */
  formatRelative: (timestamp: string) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function readStoredLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    /* storage blocked; fall through to the browser's preference */
  }
  const preferred = window.navigator.language?.toLowerCase() ?? '';
  return preferred.startsWith('ru') ? 'ru' : 'en';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = readStoredLocale();
    // Not in an effect, so translate() during the first render is correct.
    setActiveLocale(initial);
    return initial;
  });

  useEffect(() => {
    window.document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setActiveLocale(next);
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* the choice just will not survive a reload */
    }
  }, []);

  const t = useCallback(
    (key: MessageKey, params?: MessageParams) =>
      interpolate(template(locale, key), params),
    [locale],
  );

  const tNode = useCallback(
    (key: MessageKey, params: Record<string, ReactNode>): ReactNode => {
      const source = template(locale, key);
      const pieces: ReactNode[] = [];
      let cursor = 0;

      for (const match of source.matchAll(PLACEHOLDER)) {
        const name = match[1] as string;
        const at = match.index ?? 0;
        if (at > cursor) pieces.push(source.slice(cursor, at));
        pieces.push(name in params ? params[name] : match[0]);
        cursor = at + match[0].length;
      }
      if (cursor < source.length) pieces.push(source.slice(cursor));

      return pieces.map((piece, index) => (
        <Fragment key={index}>{piece}</Fragment>
      ));
    },
    [locale],
  );

  const relativeFormat = useMemo(
    () => new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }),
    [locale],
  );

  const formatRelative = useCallback(
    (timestamp: string) => {
      const then = new Date(timestamp);
      const minutes = (Date.now() - then.getTime()) / 60_000;

      if (minutes < 1) return t('common.justNow');
      if (minutes < 60) return relativeFormat.format(-Math.floor(minutes), 'minute');
      if (minutes < 60 * 24) {
        return relativeFormat.format(-Math.floor(minutes / 60), 'hour');
      }
      return then.toLocaleDateString(locale, { day: 'numeric', month: 'short' });
    },
    [locale, relativeFormat, t],
  );

  const value = useMemo<I18nContextValue>(
    () => ({ locale, locales: LOCALES, setLocale, t, tNode, formatRelative }),
    [locale, setLocale, t, tNode, formatRelative],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error('useI18n must be used inside <I18nProvider>');
  return context;
}
