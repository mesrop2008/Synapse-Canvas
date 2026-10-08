/** Holds the session and, on a 401, refreshes once and replays. */

import { getActiveLocale, translate } from '../../i18n';
import type { ErrorBody, TokenPair } from '../types/api';

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '');

const REFRESH_TOKEN_KEY = 'synapse.refresh_token';

/** Memory only. The refresh token sits in localStorage, readable by any
 *  injected script; an httpOnly cookie plus CSRF token would be safer. */
let accessToken: string | null = null;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /** e.g. the server's row alongside a PATCH 409. */
    readonly body: unknown,
    /** Null when the response is not the API's own, e.g. a proxy's. */
    readonly code: string | null = null,
    /** Seconds from a 429's Retry-After. */
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

interface SendOptions {
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  keepalive?: boolean;
}

async function send(
  method: Method,
  path: string,
  token: string | null,
  options: SendOptions = {},
): Promise<Response> {
  // Any email this request triggers is written in the active language.
  const headers: Record<string, string> = {
    'Accept-Language': getActiveLocale(),
    ...options.headers,
  };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  return fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    keepalive: options.keepalive,
  });
}

/** Flattens both FastAPI shapes: `{detail: "..."}` and `{detail: [{msg}]}`. */
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

/** One refresh for concurrent 401s, or several token pairs would race to
 *  write localStorage. */
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

/** Sends, and on a 401 refreshes once and replays. */
async function sendWithSession(
  method: Method,
  path: string,
  options: RequestOptions & SendOptions,
): Promise<Response> {
  const { authenticated = true, refreshOnUnauthorized = true } = options;

  let response = await send(method, path, authenticated ? accessToken : null, options);

  if (
    response.status === 401 &&
    authenticated &&
    refreshOnUnauthorized &&
    getRefreshToken()
  ) {
    try {
      const fresh = await refreshAccessToken();
      // Once only: a second 401 means the token was not the problem.
      response = await send(method, path, fresh, options);
    } catch {
      // Spent or revoked; nothing here can recover it.
      clearSession({ notify: true });
      throw new ApiError(401, translate('errors.sessionExpired'), null);
    }
  }
  return response;
}

async function failure(response: Response): Promise<ApiError> {
  const parsed = await readBody(response);
  return new ApiError(
    response.status,
    describe(response.status, parsed),
    parsed,
    errorCode(parsed),
    retryAfterSeconds(response),
  );
}

export async function request<T>(
  method: Method,
  path: string,
  options: RequestOptions & { body?: unknown } = {},
): Promise<T> {
  const response = await sendWithSession(method, path, options);
  if (!response.ok) throw await failure(response);
  return (await readBody(response)) as T;
}

/** For server-sent events: `request`'s auth and refresh, with the body left
 *  for the caller to read as it arrives. */
export async function openStream(
  path: string,
  options: { headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<Response> {
  const response = await sendWithSession('GET', path, {
    ...options,
    headers: { Accept: 'text/event-stream', ...options.headers },
  });
  if (!response.ok) throw await failure(response);
  return response;
}

/** Outlives the page, for `pagehide`. No refresh: there is no time for one. */
export function sendOnUnload(method: Method, path: string): void {
  send(method, path, accessToken, { keepalive: true }).catch(() => {});
}
