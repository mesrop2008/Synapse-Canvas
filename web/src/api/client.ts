/** Holds the session, attaches the access token, and recovers from an expired
 *  one by refreshing once and replaying. The rest of `api/` wraps `request()`. */

import { translate } from '../../i18n';
import type { ErrorBody, TokenPair } from '../types/api';

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '');

const REFRESH_TOKEN_KEY = 'synapse.refresh_token';

/**
 * In a module variable, so it is gone on reload and never readable from storage.
 *
 * The refresh token, by contrast, is in localStorage, which any injected script
 * can read -- an XSS becomes a stolen long-lived session. The production answer
 * is an httpOnly, Secure, SameSite cookie plus a CSRF token. Deliberate
 * simplification for Part 2; Part 6 changes it.
 */
let accessToken: string | null = null;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /** Parsed body, for endpoints returning more than `detail` -- PATCH
     *  /documents returns the server's row alongside its 409. */
    readonly body: unknown,
    /** Stable identifier from the API, translated by the language packs.
     *  Null for a response that carries no code, such as a proxy's own. */
    readonly code: string | null = null,
    /** Seconds from a 429's Retry-After, so a button can count down to the
     *  moment the server will say yes. */
    readonly retryAfter: number | null = null,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

function retryAfterSeconds(response: Response): number | null {
  // Only the delta-seconds form; the API never sends an HTTP date.
  const seconds = Number(response.headers.get('Retry-After'));
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : null;
}

function errorCode(body: unknown): string | null {
  const code = (body as ErrorBody | null)?.code;
  return typeof code === 'string' ? code : null;
}

export function getRefreshToken(): string | null {
  try {
    return window.localStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    // Throws outright when site data is blocked, not just returns null.
    return null;
  }
}

export function setSession(tokens: TokenPair): void {
  accessToken = tokens.access_token;
  try {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  } catch {
    // Session still works for this tab, just not across a reload.
  }
}

type SessionEndListener = () => void;
const sessionEndListeners = new Set<SessionEndListener>();

/** Fires when a failed refresh drops the session, not on explicit logout. */
export function onSessionEnd(listener: SessionEndListener): () => void {
  sessionEndListeners.add(listener);
  return () => sessionEndListeners.delete(listener);
}

export function clearSession(options: { notify?: boolean } = {}): void {
  accessToken = null;
  try {
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    /* nothing to clean up if storage is unavailable */
  }
  if (options.notify) {
    for (const listener of sessionEndListeners) listener();
  }
}

interface RequestOptions {
  /** False for the endpoints that establish a session in the first place. */
  authenticated?: boolean;
  /** False to stop a 401 triggering a refresh -- used by refresh itself. */
  refreshOnUnauthorized?: boolean;
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';

async function send(
  method: Method,
  path: string,
  body: unknown,
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  return fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** FastAPI returns `{detail: "..."}` from handlers and `{detail: [{loc, msg}]}`
 *  from validation; flatten both to one string. */
function describe(status: number, body: unknown): string {
  const detail = (body as ErrorBody | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: string }).msg)
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length) return messages.join('; ');
  }
  return translate('errors.status', { status });
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

let inFlightRefresh: Promise<string> | null = null;

/**
 * Collapses concurrent callers onto one request. Several queries 401-ing at once
 * is the normal case, and one refresh each would mint several token pairs racing
 * to write localStorage. The slot clears once settled, so a later expiry
 * refreshes again.
 */
function refreshAccessToken(): Promise<string> {
  inFlightRefresh ??= performRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

async function performRefresh(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new ApiError(401, translate('errors.notSignedIn'), null);

  const tokens = await request<TokenPair>('POST', '/auth/refresh', {
    body: { refresh_token: refreshToken },
    authenticated: false,
    refreshOnUnauthorized: false,
  });
  setSession(tokens);
  return tokens.access_token;
}

export async function request<T>(
  method: Method,
  path: string,
  options: RequestOptions & { body?: unknown } = {},
): Promise<T> {
  const {
    body,
    authenticated = true,
    refreshOnUnauthorized = true,
  } = options;

  let response = await send(method, path, body, authenticated ? accessToken : null);

  if (
    response.status === 401 &&
    authenticated &&
    refreshOnUnauthorized &&
    getRefreshToken()
  ) {
    try {
      const fresh = await refreshAccessToken();
      // Once only: a second 401 means the token was not the problem.
      response = await send(method, path, body, fresh);
    } catch {
      // Spent or revoked; nothing here can recover it.
      clearSession({ notify: true });
      throw new ApiError(401, translate('errors.sessionExpired'), null);
    }
  }

  const parsed = await readBody(response);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      describe(response.status, parsed),
      parsed,
      errorCode(parsed),
      retryAfterSeconds(response),
    );
  }
  return parsed as T;
}
