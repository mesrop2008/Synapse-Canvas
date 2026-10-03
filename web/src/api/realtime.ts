import type { WsTicket } from '../types/api';
import { API_BASE_URL, request } from './client';

/** A WebSocket cannot send Authorization, so it opens with a short-lived,
 *  single-use ticket. */
export function createWsTicket(documentId: string): Promise<WsTicket> {
  return request<WsTicket>('POST', `/documents/${documentId}/ws-ticket`);
}

/** Handles both a same-origin path and an absolute base URL; the scheme
 *  follows the page, so https gets wss. */
export function documentSocketUrl(documentId: string, ticket: string): string {
  const url = new URL(API_BASE_URL, window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/ws/documents/${documentId}`;
  url.search = `?ticket=${encodeURIComponent(ticket)}`;
  return url.toString();
}
