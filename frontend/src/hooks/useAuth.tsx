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
  // Starts as 'loading' only when there is a stored session worth restoring;
  // otherwise the login page would flash a spinner on every first visit.
  const [status, setStatus] = useState<AuthStatus>(() =>
    getRefreshToken() ? 'loading' : 'anonymous',
  );
  const queryClient = useQueryClient();

  // Restore the session on reload. The access token lives in memory and is
  // therefore gone, so this request goes out unauthenticated, comes back 401,
  // and the client's refresh-and-retry turns it into a signed-in response.
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

  // A refresh that failed mid-session ends it from underneath the UI.
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
    // Without this, the next account to sign in on this tab would briefly read
    // the previous one's cached workspaces and documents.
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
