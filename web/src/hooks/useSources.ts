import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { deleteSource, listSources, retrySource, uploadSource } from '../api/sources';
import type { Source } from '../types/api';
import { aiUsageKey } from './useAIUsage';
import { workspaceKeys } from './useWorkspaces';

export const sourceKeys = {
  list: (workspaceId: string) => [...workspaceKeys.detail(workspaceId), 'sources'] as const,
};

const POLL_MS = 2000;
const DONE_LINGER_MS = 1500;

function inFlight(sources: Source[] | undefined): boolean {
  return Boolean(sources?.some((s) => s.status === 'pending' || s.status === 'processing'));
}

/** Polled while any source is still being read, so pending → processing →
 *  ready shows up without a refresh; idle otherwise. */
export function useSources(workspaceId: string) {
  return useQuery({
    queryKey: sourceKeys.list(workspaceId),
    queryFn: () => listSources(workspaceId),
    refetchInterval: (query) => (inFlight(query.state.data) ? POLL_MS : false),
  });
}

function useRefreshSources(workspaceId: string) {
  const queryClient = useQueryClient();
  return useCallback(
    () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: sourceKeys.list(workspaceId) }),
        // Storage and embedding requests are counted there.
        queryClient.invalidateQueries({ queryKey: aiUsageKey(workspaceId) }),
      ]),
    [queryClient, workspaceId],
  );
}

export function useDeleteSource(workspaceId: string) {
  const refresh = useRefreshSources(workspaceId);
  return useMutation({
    mutationFn: (sourceId: string) => deleteSource(workspaceId, sourceId),
    onSuccess: refresh,
  });
}

export function useRetrySource(workspaceId: string) {
  const refresh = useRefreshSources(workspaceId);
  return useMutation({
    mutationFn: (sourceId: string) => retrySource(workspaceId, sourceId),
    onSuccess: refresh,
  });
}

export interface UploadState {
  key: string;
  name: string;
  /** 0 to 1, the bytes sent so far. */
  progress: number;
  status: 'uploading' | 'done' | 'duplicate' | 'error';
  error: unknown;
  /** The existing source, for a duplicate. */
  source: Source | null;
}

/** One upload per file, side by side, each with its own progress and outcome. */
export function useUploads(workspaceId: string) {
  const refresh = useRefreshSources(workspaceId);
  const [uploads, setUploads] = useState<UploadState[]>([]);

  const patch = useCallback((key: string, change: Partial<UploadState>) => {
    setUploads((current) =>
      current.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );
  }, []);

  const dismiss = useCallback((key: string) => {
    setUploads((current) => current.filter((item) => item.key !== key));
  }, []);

  const start = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`;
        setUploads((current) => [
          ...current,
          { key, name: file.name, progress: 0, status: 'uploading', error: null, source: null },
        ]);
        uploadSource(workspaceId, file, {
          onProgress: (progress) => patch(key, { progress }),
        })
          .then(({ source, created }) => {
            patch(key, { progress: 1, status: created ? 'done' : 'duplicate', source });
            void refresh();
            // The list shows it from here on; a duplicate's note stays.
            if (created) window.setTimeout(() => dismiss(key), DONE_LINGER_MS);
          })
          .catch((error: unknown) => patch(key, { status: 'error', error }));
      }
    },
    [workspaceId, patch, refresh, dismiss],
  );

  return { uploads, start, dismiss };
}
