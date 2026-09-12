import type { Workspace } from '../types/api';
import { request } from './client';

export function listWorkspaces(): Promise<Workspace[]> {
  return request<Workspace[]>('GET', '/workspaces');
}

export function getWorkspace(workspaceId: string): Promise<Workspace> {
  return request<Workspace>('GET', `/workspaces/${workspaceId}`);
}

export function createWorkspace(name: string): Promise<Workspace> {
  return request<Workspace>('POST', '/workspaces', { body: { name } });
}
