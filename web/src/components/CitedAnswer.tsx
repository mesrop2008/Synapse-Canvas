import { Fragment, type ReactNode } from 'react';

import { useI18n } from '../hooks/useI18n';
import type { AIQuery, Citation } from '../types/api';
import { Alert } from './Alert';
import { useSourceViewer } from './sourceViewer/SourceViewer';

// As the server reads them: [1], [1, 2], [1; 3].
const MARKERS = /\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]/g;

/** Markers the server resolved become buttons; any other number in brackets
 *  is one the model invented, and is left out. */
function withMarkers(
  text: string,
  byMarker: Map<number, Citation>,
  render: (citation: Citation) => ReactNode,
): ReactNode[] {
  const pieces: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MARKERS)) {
    const at = match.index ?? 0;
    pieces.push(text.slice(cursor, at));
    const numbers = (match[1] ?? '').split(/\s*[,;]\s*/).map(Number);
    for (const number of numbers) {
      const citation = byMarker.get(number);
      if (citation) pieces.push(render(citation));
    }
    cursor = at + match[0].length;
  }
  pieces.push(text.slice(cursor));
  return pieces.map((piece, index) => <Fragment key={index}>{piece}</Fragment>);
}

interface CitedAnswerProps {
  workspaceId: string;
  query: AIQuery;
  text: string;
  citations: Citation[];
}

export function CitedAnswer({ workspaceId, query, text, citations }: CitedAnswerProps) {
  const { t } = useI18n();
  const openViewer = useSourceViewer();
  // The viewer opens at the passage, scrolled to it and highlighted.
  const open = (citation: Citation) =>
    openViewer({
      workspaceId,
      sourceId: citation.source_id,
      filename: citation.filename,
      citation,
    });
  const searched = query.retrieved_chunk_ids !== null;
  const retrieved = query.retrieved_chunk_ids?.length ?? 0;
  const byMarker = new Map(citations.map((citation) => [citation.marker, citation]));

  const chip = (citation: Citation) => (
    <button
      type="button"
      className="cite"
      data-unavailable={citation.available === false ? 'true' : undefined}
      aria-label={t('ai.citations.open', { marker: citation.marker })}
      onClick={() => open(citation)}
    >
      {citation.marker}
    </button>
  );

  return (
    <>
      <p className="ai-text">{searched ? withMarkers(text, byMarker, chip) : text}</p>

      {searched && retrieved === 0 && query.mode === 'ask' && (
        <Alert kind="warn">{t('ai.citations.notGrounded')}</Alert>
      )}
      {searched && retrieved > 0 && citations.length === 0 && (
        <p className="hint">{t('ai.citations.noneCited', { count: retrieved })}</p>
      )}

      {citations.length > 0 && (
        <section className="cited" aria-label={t('ai.citations.title')}>
          <h3 className="ai-label">{t('ai.citations.title')}</h3>
          <ol className="cited-list">
            {citations.map((citation) => (
              <li key={citation.marker}>
                {chip(citation)}
                <button
                  type="button"
                  className="btn-link cited-name"
                  onClick={() => open(citation)}
                >
                  {citation.page_number !== null
                    ? t('ai.citations.item', {
                        filename: citation.filename,
                        page: citation.page_number,
                      })
                    : t('ai.citations.itemNoPage', { filename: citation.filename })}
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
