import type { Member, Workspace, WorkspaceRole } from '../types/api';
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

export function renameWorkspace(
  workspaceId: string,
  name: string,
): Promise<Workspace> {
  return request<Workspace>('PATCH', `/workspaces/${workspaceId}`, {
    body: { name },
  });
}

export function deleteWorkspace(workspaceId: string): Promise<void> {
  return request<void>('DELETE', `/workspaces/${workspaceId}`);
}

export function listMembers(workspaceId: string): Promise<Member[]> {
  return request<Member[]>('GET', `/workspaces/${workspaceId}/members`);
}

/** By email; the API refuses an unconfirmed address. */
export function addMember(
  workspaceId: string,
  email: string,
  role: WorkspaceRole,
): Promise<Member> {
  return request<Member>('POST', `/workspaces/${workspaceId}/members`, {
    body: { email, role },
  });
}

export function removeMember(
  workspaceId: string,
  userId: string,
): Promise<void> {
  return request<void>('DELETE', `/workspaces/${workspaceId}/members/${userId}`);
}
