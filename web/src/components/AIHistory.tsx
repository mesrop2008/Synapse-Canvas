import { useQuery } from '@tanstack/react-query';

import { listQueries } from '../api/ai';
import { errorMessage } from '../api/errors';
import { useI18n } from '../hooks/useI18n';
import type { MessageKey } from '../../i18n';
import type { AIQueryMode } from '../types/api';
import { Alert } from './Alert';
import { CitedAnswer } from './CitedAnswer';

const SHOWN = 5;

const MODE_LABELS: Record<AIQueryMode, MessageKey> = {
  continue: 'ai.modes.continue',
  rewrite: 'ai.modes.rewrite',
  summarize: 'ai.modes.summarize',
  ask: 'ai.modes.ask',
  sources: 'ai.modes.sources',
};

export const aiHistoryKey = (documentId: string) =>
  ['documents', documentId, 'ai-history'] as const;

/** The caller's last few finished answers, with their citations as stored:
 *  one whose source has been deleted since shows as unavailable. */
export function AIHistory({
  documentId,
  workspaceId,
  currentId,
}: {
  documentId: string;
  workspaceId: string;
  /** The answer on screen above, left out here. */
  currentId: string | null;
}) {
  const { t, formatRelative } = useI18n();
  const history = useQuery({
    queryKey: aiHistoryKey(documentId),
    queryFn: () => listQueries(documentId, SHOWN + 1),
  });

  const answers = (history.data?.items ?? [])
    .filter((query) => query.status === 'completed' && query.response && query.id !== currentId)
    .slice(0, SHOWN);

  if (history.error) return <Alert>{errorMessage(history.error)}</Alert>;
  if (!answers.length) return null;

  return (
    <details className="ai-history">
      <summary>{t('ai.history.title')}</summary>
      <ul>
        {answers.map((query) => (
          <li key={query.id}>
            <details>
              <summary>
                <span className="ai-history-prompt">{query.prompt || t(MODE_LABELS[query.mode])}</span>
                <span className="meta">
                  {t('ai.history.item', {
                    mode: t(MODE_LABELS[query.mode]),
                    when: formatRelative(query.created_at),
                  })}
                </span>
              </summary>
              <CitedAnswer
                workspaceId={workspaceId}
                query={query}
                text={query.response ?? ''}
                citations={query.citations}
              />
            </details>
          </li>
        ))}
      </ul>
    </details>
  );
}
