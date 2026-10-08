import type {
  AIQuery,
  AIQueryInput,
  AIStreamEvent,
  DocumentDetail,
  WorkspaceUsage,
} from '../types/api';
import { openStream, request, sendOnUnload } from './client';

const queries = (documentId: string) => `/documents/${documentId}/ai/queries`;

export function createQuery(documentId: string, input: AIQueryInput): Promise<AIQuery> {
  return request<AIQuery>('POST', queries(documentId), { body: input });
}

export function cancelQuery(documentId: string, queryId: string): Promise<AIQuery> {
  return request<AIQuery>('POST', `${queries(documentId)}/${queryId}/cancel`);
}

export function cancelQueryOnUnload(documentId: string, queryId: string): void {
  sendOnUnload('POST', `${queries(documentId)}/${queryId}/cancel`);
}

export interface ApplyTarget {
  /** The version the editor shows; anything else is a 409. */
  version: number;
  selection_from?: number;
  selection_to?: number;
}

export function applyQuery(
  documentId: string,
  queryId: string,
  target: ApplyTarget,
): Promise<DocumentDetail> {
  return request<DocumentDetail>('POST', `${queries(documentId)}/${queryId}/apply`, {
    body: target,
  });
}

export function getUsage(workspaceId: string): Promise<WorkspaceUsage> {
  return request<WorkspaceUsage>('GET', `/workspaces/${workspaceId}/usage`);
}

/**
 * Reads the response as server-sent events with fetch, not EventSource:
 * EventSource cannot send an Authorization header, and this keeps the one
 * auth path, refresh included. Resuming is ours to do, via Last-Event-ID.
 *
 * Resolves when the server closes the stream, terminal event or not.
 */
export async function streamQuery(
  documentId: string,
  queryId: string,
  options: {
    signal: AbortSignal;
    lastEventId: string | null;
    onEvent: (event: AIStreamEvent, id: string | null) => void;
  },
): Promise<void> {
  const response = await openStream(`${queries(documentId)}/${queryId}/stream`, {
    signal: options.signal,
    headers: options.lastEventId ? { 'Last-Event-ID': options.lastEventId } : {},
  });
  if (!response.body) return;

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value.replace(/\r\n/g, '\n');

    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      let type = 'message';
      let data: string | null = null;
      let id: string | null = null;
      for (const line of block.split('\n')) {
        // Lines starting with ':' are keepalive comments.
        if (line.startsWith('event: ')) type = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
        else if (line.startsWith('id: ')) id = line.slice(4);
      }
      if (data !== null) {
        options.onEvent({ type, ...JSON.parse(data) } as AIStreamEvent, id);
      }
    }
  }
}
