import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';

import * as authApi from '../api/auth';
import { clearSession, getRefreshToken, onSessionEnd } from '../api/client';
import type { User } from '../types/api';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

interface AuthContextValue {
  user: User | null;
  status: AuthStatus;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  // 'loading' only when a session is worth restoring, or the login page
  // flashes a spinner on every first visit.
  const [status, setStatus] = useState<AuthStatus>(() =>
    getRefreshToken() ? 'loading' : 'anonymous',
  );
  const queryClient = useQueryClient();

  // The access token is memory-only and therefore gone, so this goes out
  // unauthenticated, 401s, and the client's refresh-and-retry signs it in.
  useEffect(() => {
    if (status !== 'loading') return;
    let cancelled = false;

    authApi
      .fetchCurrentUser()
      .then((restored) => {
        if (cancelled) return;
        setUser(restored);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        clearSession();
        setStatus('anonymous');
      });

    return () => {
      cancelled = true;
    };
    // Runs once: `status` leaves 'loading' for good.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A failed refresh ends the session from underneath the UI.
  useEffect(
    () =>
      onSessionEnd(() => {
        setUser(null);
        setStatus('anonymous');
        queryClient.clear();
      }),
    [queryClient],
  );

  const signIn = useCallback(
    async (email: string, password: string) => {
      await authApi.login(email, password);
      setUser(await authApi.fetchCurrentUser());
      setStatus('authenticated');
    },
    [],
  );

  const signOut = useCallback(async () => {
    await authApi.logout();
    setUser(null);
    setStatus('anonymous');
    // Or the next account on this tab reads the previous one's cache.
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, signIn, signOut }),
    [user, status, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>');
  return context;
}
