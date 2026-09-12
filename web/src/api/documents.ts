import type {
  DocumentDetail,
  DocumentSummary,
  DocumentVersionConflict,
  ProseMirrorDoc,
} from '../types/api';
import { ApiError, request } from './client';

export function listDocuments(workspaceId: string): Promise<DocumentSummary[]> {
  return request<DocumentSummary[]>('GET', `/workspaces/${workspaceId}/documents`);
}

export function getDocument(
  workspaceId: string,
  documentId: string,
): Promise<DocumentDetail> {
  return request<DocumentDetail>(
    'GET',
    `/workspaces/${workspaceId}/documents/${documentId}`,
  );
}

export function createDocument(
  workspaceId: string,
  title: string,
): Promise<DocumentDetail> {
  return request<DocumentDetail>('POST', `/workspaces/${workspaceId}/documents`, {
    body: { title },
  });
}

export interface DocumentPatch {
  /** The version the edit was computed against. A mismatch is a 409. */
  version: number;
  title?: string;
  content?: ProseMirrorDoc;
}

export function updateDocument(
  workspaceId: string,
  documentId: string,
  patch: DocumentPatch,
): Promise<DocumentDetail> {
  return request<DocumentDetail>(
    'PATCH',
    `/workspaces/${workspaceId}/documents/${documentId}`,
    { body: patch },
  );
}

export function deleteDocument(
  workspaceId: string,
  documentId: string,
): Promise<void> {
  return request<void>(
    'DELETE',
    `/workspaces/${workspaceId}/documents/${documentId}`,
  );
}

/**
 * Narrows a failed PATCH to the stale-version case, whose body carries the
 * server's current row. Checked structurally rather than on status alone,
 * because `current` is what callers actually need and its absence would
 * otherwise surface as a runtime undefined.
 */
export function asVersionConflict(
  error: unknown,
): DocumentVersionConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body as DocumentVersionConflict | null;
  return body && typeof body === 'object' && body.current ? body : null;
}
