import type { Source, SourceChunk } from '../types/api';
import { request, upload } from './client';

const sources = (workspaceId: string) => `/workspaces/${workspaceId}/sources`;

export function listSources(workspaceId: string): Promise<Source[]> {
  return request<Source[]>('GET', sources(workspaceId));
}

export interface Uploaded {
  source: Source;
  /** False when the workspace already held this exact file. */
  created: boolean;
}

export async function uploadSource(
  workspaceId: string,
  file: File,
  options: { onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<Uploaded> {
  const form = new FormData();
  form.append('file', file, file.name);
  const { status, body } = await upload<Source>(sources(workspaceId), form, options);
  return { source: body, created: status === 201 };
}

export function deleteSource(workspaceId: string, sourceId: string): Promise<void> {
  return request<void>('DELETE', `${sources(workspaceId)}/${sourceId}`);
}

export function retrySource(workspaceId: string, sourceId: string): Promise<Source> {
  return request<Source>('POST', `${sources(workspaceId)}/${sourceId}/retry`);
}

export function getChunk(
  workspaceId: string,
  sourceId: string,
  chunkId: string,
): Promise<SourceChunk> {
  return request<SourceChunk>('GET', `${sources(workspaceId)}/${sourceId}/chunks/${chunkId}`);
}
