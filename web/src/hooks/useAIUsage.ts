import { useQuery } from '@tanstack/react-query';

import { getUsage } from '../api/ai';
import { workspaceKeys } from './useWorkspaces';

export const aiUsageKey = (workspaceId: string) =>
  [...workspaceKeys.detail(workspaceId), 'ai-usage'] as const;

/** Refetched on focus and each minute: other members spend from it too. */
export function useAIUsage(workspaceId: string) {
  return useQuery({
    queryKey: aiUsageKey(workspaceId),
    queryFn: () => getUsage(workspaceId),
    refetchInterval: 60_000,
  });
}
