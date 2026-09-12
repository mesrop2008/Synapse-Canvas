import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
} from '../api/documents';

export const documentKeys = {
  list: (workspaceId: string) =>
    ['workspaces', workspaceId, 'documents'] as const,
  detail: (workspaceId: string, documentId: string) =>
    ['workspaces', workspaceId, 'documents', documentId] as const,
};

export function useDocuments(workspaceId: string) {
  return useQuery({
    queryKey: documentKeys.list(workspaceId),
    queryFn: () => listDocuments(workspaceId),
  });
}

export function useDocument(workspaceId: string, documentId: string) {
  return useQuery({
    queryKey: documentKeys.detail(workspaceId, documentId),
    queryFn: () => getDocument(workspaceId, documentId),
    // The editor holds the loaded content as its own state, so a background
    // refetch would be thrown away at best and would fight the user at worst.
    staleTime: Infinity,
    refetchOnMount: false,
  });
}

export function useCreateDocument(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => createDocument(workspaceId, title),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: documentKeys.list(workspaceId) }),
  });
}

export function useDeleteDocument(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => deleteDocument(workspaceId, documentId),
    onSuccess: (_result, documentId) => {
      queryClient.removeQueries({
        queryKey: documentKeys.detail(workspaceId, documentId),
      });
      return queryClient.invalidateQueries({
        queryKey: documentKeys.list(workspaceId),
      });
    },
  });
}
