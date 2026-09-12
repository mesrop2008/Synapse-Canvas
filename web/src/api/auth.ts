import type { AcceptedResponse, TokenPair, User } from '../types/api';
import { clearSession, getRefreshToken, request, setSession } from './client';

export async function login(email: string, password: string): Promise<TokenPair> {
  const tokens = await request<TokenPair>('POST', '/auth/login', {
    body: { email, password },
    authenticated: false,
  });
  setSession(tokens);
  return tokens;
}

/**
 * Answers 202 whether or not the address was already taken -- the backend will
 * not say, so the UI cannot either. Verification is required before login.
 */
export function register(
  email: string,
  password: string,
  name: string,
): Promise<AcceptedResponse> {
  return request<AcceptedResponse>('POST', '/auth/register', {
    body: { email, password, name },
    authenticated: false,
  });
}

export function verifyEmail(token: string): Promise<User> {
  return request<User>('POST', '/auth/verify-email', {
    body: { token },
    authenticated: false,
  });
}

export function resendVerification(email: string): Promise<AcceptedResponse> {
  return request<AcceptedResponse>('POST', '/auth/resend-verification', {
    body: { email },
    authenticated: false,
  });
}

export function fetchCurrentUser(): Promise<User> {
  return request<User>('GET', '/auth/me');
}

/** Revokes the stored refresh token server-side, then drops it locally. */
export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    try {
      await request<void>('POST', '/auth/logout', {
        body: { refresh_token: refreshToken },
        authenticated: false,
      });
    } catch {
      // Signing out must succeed locally even if the call does not.
    }
  }
  clearSession();
}
