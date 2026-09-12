/**
 * The one place that talks to the API.
 *
 * Responsibilities: hold the session, attach the access token, and recover from
 * a single expired access token by refreshing once and replaying the request.
 * Everything else in `api/` is a thin typed wrapper over `request()`.
 */

import type { ErrorBody, TokenPair } from '../types/api';

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '');

const REFRESH_TOKEN_KEY = 'synapse.refresh_token';

/**
 * The access token lives in a module variable, so it is gone on reload and
 * never readable from storage.
 *
 * The refresh token lives in localStorage, which IS readable by any script that
 * gets injected into this origin -- an XSS becomes a stolen long-lived session.
 * The production answer is an httpOnly, Secure, SameSite cookie set by the
 * backend, which JavaScript cannot read at all (and a CSRF token to go with
 * it). This is a deliberate simplification to keep Part 2 to one origin and no
 * cookie plumbing; Part 6 is where it changes.
 */
let accessToken: string | null = null;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /** Parsed response body, for endpoints that return more than `detail` --
     *  PATCH /documents returns the server's row alongside its 409. */
    readonly body: unknown,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

export function getRefreshToken(): string | null {
  try {
    return window.localStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    // Storage can throw outright when cookies/site data are blocked.
    return null;
  }
}

export function setSession(tokens: TokenPair): void {
  accessToken = tokens.access_token;
  try {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  } catch {
    // Session still works for this tab; it just will not survive a reload.
  }
}

type SessionEndListener = () => void;
const sessionEndListeners = new Set<SessionEndListener>();

/** Fires when the session is dropped from underneath the UI -- a refresh that
 *  failed, not a user-initiated logout. The auth provider uses it to redirect. */
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
  /** False to stop a 401 from triggering a refresh -- used by refresh itself. */
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

/**
 * Flattens FastAPI's two error shapes into one string: `{detail: "..."}` from
 * the app's own handlers, and `{detail: [{loc, msg}, ...]}` from validation.
 */
function describe(status: number, body: unknown): string {
  const detail = (body as ErrorBody | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: string }).msg)
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length) return messages.join('; ');
  }
  return `Request failed with status ${status}`;
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
 * Refreshes the access token, collapsing concurrent callers onto one request.
 *
 * Several queries failing with 401 at the same moment is the normal case, and
 * firing one refresh each would mean several token pairs issued and several
 * writes to localStorage racing each other. The first caller stores its promise
 * and the rest await it; the slot is cleared once it settles, so the next real
 * expiry refreshes again.
 */
function refreshAccessToken(): Promise<string> {
  inFlightRefresh ??= performRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

async function performRefresh(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new ApiError(401, 'Not signed in', null);

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
      // Exactly one retry. A second 401 means the new token is not the problem,
      // and retrying again would loop.
      response = await send(method, path, body, fresh);
    } catch {
      // The refresh token is spent or revoked; nothing here can recover it.
      clearSession({ notify: true });
      throw new ApiError(401, 'Your session has expired. Please sign in again.', null);
    }
  }

  const parsed = await readBody(response);
  if (!response.ok) {
    throw new ApiError(response.status, describe(response.status, parsed), parsed);
  }
  return parsed as T;
}
