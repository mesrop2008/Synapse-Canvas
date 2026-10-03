import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  addMember,
  createWorkspace,
  deleteWorkspace,
  getWorkspace,
  listMembers,
  listWorkspaces,
  removeMember,
  renameWorkspace,
} from '../api/workspaces';
import type { WorkspaceRole } from '../types/api';

/** In one place so an invalidation cannot miss a cache by typo. */
export const workspaceKeys = {
  all: ['workspaces'] as const,
  detail: (workspaceId: string) => ['workspaces', workspaceId] as const,
  members: (workspaceId: string) =>
    ['workspaces', workspaceId, 'members'] as const,
};

export function useWorkspaces() {
  return useQuery({ queryKey: workspaceKeys.all, queryFn: listWorkspaces });
}

export function useWorkspace(workspaceId: string) {
  return useQuery({
    queryKey: workspaceKeys.detail(workspaceId),
    queryFn: () => getWorkspace(workspaceId),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => createWorkspace(name),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workspaceKeys.all }),
  });
}

export function useDeleteWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workspaceId: string) => deleteWorkspace(workspaceId),
    onSuccess: (_result, workspaceId) => {
      // Removed, not invalidated: refetching would 404. Clears documents too.
      queryClient.removeQueries({ queryKey: workspaceKeys.detail(workspaceId) });
      return queryClient.invalidateQueries({
        queryKey: workspaceKeys.all,
        exact: true,
      });
    },
  });
}

export function useMembers(workspaceId: string) {
  return useQuery({
    queryKey: workspaceKeys.members(workspaceId),
    queryFn: () => listMembers(workspaceId),
  });
}

export function useRenameWorkspace(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => renameWorkspace(workspaceId, name),
    onSuccess: (renamed) => {
      // The response is the row. `exact`: the key prefixes member/document keys.
      queryClient.setQueryData(workspaceKeys.detail(workspaceId), renamed);
      return queryClient.invalidateQueries({
        queryKey: workspaceKeys.all,
        exact: true,
      });
    },
  });
}

interface AddMemberInput {
  email: string;
  role: WorkspaceRole;
}

export function useAddMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: AddMemberInput) =>
      addMember(workspaceId, email, role),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: workspaceKeys.members(workspaceId),
        exact: true,
      }),
  });
}

export function useRemoveMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => removeMember(workspaceId, userId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: workspaceKeys.members(workspaceId),
        exact: true,
      }),
  });
}
