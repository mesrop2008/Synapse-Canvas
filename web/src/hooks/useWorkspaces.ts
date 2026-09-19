import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createWorkspace,
  deleteWorkspace,
  getWorkspace,
  listWorkspaces,
} from '../api/workspaces';

/** In one place so an invalidation cannot miss a cache by typo. */
export const workspaceKeys = {
  all: ['workspaces'] as const,
  detail: (workspaceId: string) => ['workspaces', workspaceId] as const,
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
      // Removed rather than invalidated: the workspace and every document
      // under it are gone, so refetching them would only produce 404s. The
      // detail key is a prefix of the document keys, so this clears both.
      queryClient.removeQueries({ queryKey: workspaceKeys.detail(workspaceId) });
      return queryClient.invalidateQueries({
        queryKey: workspaceKeys.all,
        exact: true,
      });
    },
  });
}
