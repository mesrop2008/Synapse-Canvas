import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type Theme = 'light' | 'dark';

/** Shared with the bootstrap script in index.html; changing it changes both. */
const STORAGE_KEY = 'synapse.theme';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (next: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/** Null until the user picks. "Follow the system" is the starting state, not a
 *  third button. */
function readStoredTheme(): Theme | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    /* storage blocked; treat as no choice made */
  }
  // Anything else, including the 'system' this used to store, means unset.
  return null;
}

const systemQuery = () => window.matchMedia('(prefers-color-scheme: dark)');

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [chosen, setChosen] = useState<Theme | null>(readStoredTheme);
  const [systemIsDark, setSystemIsDark] = useState(() => systemQuery().matches);

  // Only matters while nothing is chosen; unconditional avoids resubscribing.
  useEffect(() => {
    const query = systemQuery();
    const onChange = (event: MediaQueryListEvent) => setSystemIsDark(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  const theme: Theme = chosen ?? (systemIsDark ? 'dark' : 'light');

  useEffect(() => {
    const root = window.document.documentElement;
    root.dataset.theme = theme;
    // Paints native controls and scrollbars to match.
    root.style.colorScheme = theme;
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setChosen(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* the choice just will not survive a reload */
    }
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used inside <ThemeProvider>');
  return context;
}
