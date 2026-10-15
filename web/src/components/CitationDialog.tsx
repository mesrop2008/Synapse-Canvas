import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';

import { ApiError } from '../api/client';
import { errorMessage } from '../api/errors';
import { getChunk } from '../api/sources';
import { useI18n } from '../hooks/useI18n';
import type { Citation } from '../types/api';
import { Alert } from './Alert';

interface CitationDialogProps {
  workspaceId: string;
  citation: Citation;
  onClose: () => void;
}

/** The passage a citation points at, fetched each time it is opened: a
 *  source deleted since answers 404 and is shown as gone, not as an error. */
export function CitationDialog({ workspaceId, citation, onClose }: CitationDialogProps) {
  const { t } = useI18n();
  const close = useRef<HTMLButtonElement>(null);
  const known = citation.available !== false;
  const chunk = useQuery({
    queryKey: ['workspaces', workspaceId, 'chunks', citation.chunk_id],
    queryFn: () => getChunk(workspaceId, citation.source_id, citation.chunk_id),
    enabled: known,
    retry: false,
  });
  const gone =
    !known || (chunk.error instanceof ApiError && chunk.error.status === 404);

  useEffect(() => {
    close.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const title = t('ai.citations.passageTitle', { marker: citation.marker });

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label={title}>
        <h2 className="modal-title">{title}</h2>
        <p className="passage-source">
          <strong>{citation.filename}</strong>
          {citation.page_number !== null && (
            <span className="meta"> · {t('ai.citations.page', { page: citation.page_number })}</span>
          )}
        </p>

        {gone ? (
          <Alert kind="warn">{t('ai.citations.unavailable')}</Alert>
        ) : chunk.error ? (
          <Alert>{errorMessage(chunk.error)}</Alert>
        ) : chunk.data ? (
          <blockquote className="passage">{chunk.data.content}</blockquote>
        ) : (
          <p className="placeholder">{t('ai.citations.loading')}</p>
        )}

        <div className="modal-actions">
          <button ref={close} type="button" className="btn btn-secondary" onClick={onClose}>
            {t('ai.citations.close')}
          </button>
        </div>
      </div>
    </div>
  );
}
