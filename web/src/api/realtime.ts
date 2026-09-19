import type { WsTicket } from '../types/api';
import { API_BASE_URL, request } from './client';

/**
 * A WebSocket cannot carry an Authorization header, so the socket is opened
 * with a ticket instead: minted by this authenticated call, good for one
 * connection and about thirty seconds.
 */
export function createWsTicket(documentId: string): Promise<WsTicket> {
  return request<WsTicket>('POST', `/documents/${documentId}/ws-ticket`);
}

/**
 * `API_BASE_URL` is either a same-origin path (`/api`, proxied by the web
 * container's nginx) or an absolute URL (the dev server talking to :8000).
 * Resolving it against the page's own origin handles both, and the scheme
 * follows the page so an https deployment does not open an insecure socket.
 */
export function documentSocketUrl(documentId: string, ticket: string): string {
  const url = new URL(API_BASE_URL, window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/ws/documents/${documentId}`;
  url.search = `?ticket=${encodeURIComponent(ticket)}`;
  return url.toString();
}
