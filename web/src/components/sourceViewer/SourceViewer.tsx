import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useQuery } from '@tanstack/react-query';

import { ApiError } from '../../api/client';
import { codeMessage, errorMessage } from '../../api/errors';
import { getSource, getSourceFile, getSourceText } from '../../api/sources';
import { saveFile } from '../../download';
import { useI18n } from '../../hooks/useI18n';
import { citationTarget, chunkTarget, type Target } from '../../sourceViewer/locate';
import type { Citation, Source, SourceText } from '../../types/api';
import { Alert } from '../Alert';
import { DownloadIcon, MinusIcon, PlusIcon } from '../icons';
import { DomView } from './DomView';

const PdfView = lazy(() => import('./PdfView'));

export interface ViewerRequest {
  workspaceId: string;
  sourceId: string;
  filename: string;
  /** Opens at the chunk it cites. */
  citation?: Citation;
}

const ViewerContext = createContext<((request: ViewerRequest) => void) | null>(null);

export function SourceViewerProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ViewerRequest | null>(null);
  const close = useCallback(() => setRequest(null), []);
  return (
    <ViewerContext.Provider value={setRequest}>
      {children}
      {request && (
        <SourceViewer
          key={`${request.sourceId}:${request.citation?.chunk_id ?? ''}`}
          request={request}
          onClose={close}
        />
      )}
    </ViewerContext.Provider>
  );
}

export function useSourceViewer(): (request: ViewerRequest) => void {
  const open = useContext(ViewerContext);
  if (!open) throw new Error('useSourceViewer must be used inside <SourceViewerProvider>');
  return open;
}

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

/** What the viewer can show by itself; anything else is offered as a
 *  download. Uploads are sniffed to these, but a row can outlive that. */
export function viewable(contentType: string): 'pdf' | 'dom' | null {
  if (contentType === 'application/pdf') return 'pdf';
  if (contentType.startsWith('text/')) return 'dom';
  if (contentType.startsWith('application/vnd.openxmlformats-officedocument.wordprocessingml'))
    return 'dom';
  return null;
}

function SourceViewer({ request, onClose }: { request: ViewerRequest; onClose: () => void }) {
  const { t } = useI18n();
  const { workspaceId, sourceId } = request;
  const closeButton = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);

  const source = useQuery({
    queryKey: ['workspaces', workspaceId, 'sources', sourceId],
    queryFn: () => getSource(workspaceId, sourceId),
  });
  const kind = source.data ? viewable(source.data.content_type) : null;
  const text = useQuery({
    queryKey: ['workspaces', workspaceId, 'sources', sourceId, 'text'],
    queryFn: () => getSourceText(workspaceId, sourceId),
    enabled: kind !== null,
    staleTime: 60_000,
  });
  const file = useQuery({
    queryKey: ['workspaces', workspaceId, 'sources', sourceId, 'file'],
    queryFn: () => getSourceFile(workspaceId, sourceId),
    enabled: kind === 'pdf',
    staleTime: Infinity,
  });

  const [chosen, setChosen] = useState<Target | null>(null);
  const cited = useMemo(
    () => (text.data && request.citation ? citationTarget(text.data, request.citation) : null),
    [text.data, request.citation],
  );
  const target = chosen ?? cited;
  const citationMissing = Boolean(request.citation && text.data && !cited);

  const [page, setPage] = useState(request.citation?.page_number ?? 1);
  const [pageCount, setPageCount] = useState(0);
  useEffect(() => {
    if (target?.page) setPage(target.page);
  }, [target]);

  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState('');
  const [current, setCurrent] = useState(0);
  const [matchCount, setMatchCount] = useState(0);
  const [showChunks, setShowChunks] = useState(true);
  const [pdfFailed, setPdfFailed] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState<unknown>(null);

  useEffect(() => setCurrent(0), [query]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        searchInput.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previous?.focus();
    };
  }, [onClose]);

  async function handleDownload() {
    setDownloadFailed(null);
    try {
      const downloaded = file.data ?? (await getSourceFile(workspaceId, sourceId));
      saveFile(downloaded.blob, downloaded.filename ?? request.filename);
    } catch (cause) {
      setDownloadFailed(cause);
    }
  }

  const step = (delta: number) =>
    matchCount && setCurrent((value) => (value + delta + matchCount) % matchCount);
  const zoomIndex = ZOOMS.indexOf(zoom);
  const view = {
    target,
    query,
    current,
    onMatches: setMatchCount,
    showChunks,
    zoom,
  };
  const unsupported =
    (source.data && kind === null) ||
    (text.error instanceof ApiError && text.error.code === 'source.not_viewable');
  const sourceGone = source.error instanceof ApiError && source.error.status === 404;

  return (
    <div
      className="modal-backdrop viewer-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="viewer"
        role="dialog"
        aria-modal="true"
        aria-label={t('sources.viewer.title', { name: request.filename })}
      >
        <header className="viewer-head">
          <h2 className="viewer-title">{request.filename}</h2>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleDownload}
            disabled={sourceGone}
          >
            <DownloadIcon />
            {t('sources.viewer.download')}
          </button>
          <button ref={closeButton} type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            {t('common.close')}
          </button>
        </header>

        {kind && !unsupported && (
          <div className="viewer-tools" role="toolbar" aria-label={t('sources.viewer.tools')}>
            {kind === 'pdf' && pageCount > 0 && (
              <div className="viewer-tool-group">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  {t('sources.viewer.previousPage')}
                </button>
                <input
                  className="input viewer-page-input"
                  type="number"
                  aria-label={t('sources.viewer.pageLabel')}
                  min={1}
                  max={pageCount}
                  value={page}
                  onChange={(event) => {
                    const next = Number(event.target.value);
                    if (next >= 1 && next <= pageCount) setPage(next);
                  }}
                />
                <span className="meta">{t('sources.viewer.pageOf', { count: pageCount })}</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={page >= pageCount}
                  onClick={() => setPage(page + 1)}
                >
                  {t('sources.viewer.nextPage')}
                </button>
              </div>
            )}

            <div className="viewer-tool-group">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={t('sources.viewer.zoomOut')}
                title={t('sources.viewer.zoomOut')}
                disabled={zoomIndex <= 0}
                onClick={() => setZoom(ZOOMS[zoomIndex - 1]!)}
              >
                <MinusIcon />
              </button>
              <span className="meta viewer-zoom">
                {t('sources.viewer.zoomLevel', { percent: Math.round(zoom * 100) })}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                aria-label={t('sources.viewer.zoomIn')}
                title={t('sources.viewer.zoomIn')}
                disabled={zoomIndex >= ZOOMS.length - 1}
                onClick={() => setZoom(ZOOMS[zoomIndex + 1]!)}
              >
                <PlusIcon />
              </button>
            </div>

            <div className="viewer-tool-group viewer-search">
              <input
                ref={searchInput}
                className="input"
                type="search"
                aria-label={t('sources.viewer.search')}
                placeholder={t('sources.viewer.searchPlaceholder')}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    step(event.shiftKey ? -1 : 1);
                  }
                }}
              />
              {query.trim() && (
                <span className="meta" aria-live="polite">
                  {matchCount
                    ? t('sources.viewer.matchOf', { current: current + 1, count: matchCount })
                    : t('sources.viewer.noMatches')}
                </span>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={!matchCount}
                onClick={() => step(-1)}
              >
                {t('sources.viewer.previousMatch')}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={!matchCount}
                onClick={() => step(1)}
              >
                {t('sources.viewer.nextMatch')}
              </button>
            </div>

            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-pressed={showChunks}
              onClick={() => setShowChunks((shown) => !shown)}
            >
              {t('sources.viewer.showChunks')}
            </button>
          </div>
        )}

        {downloadFailed !== null && (
          <Alert onDismiss={() => setDownloadFailed(null)}>{errorMessage(downloadFailed)}</Alert>
        )}
        {citationMissing && <Alert kind="warn">{t('sources.viewer.citationMissing')}</Alert>}

        <div className="viewer-body">
          <div className="viewer-document">
            {sourceGone ? (
              <Alert kind="warn">{t('ai.citations.unavailable')}</Alert>
            ) : source.error ? (
              <Alert>{errorMessage(source.error)}</Alert>
            ) : unsupported || pdfFailed ? (
              <Fallback
                message={t(pdfFailed ? 'sources.viewer.pdfFailed' : 'sources.viewer.unsupported')}
                onDownload={handleDownload}
              />
            ) : kind === 'pdf' ? (
              file.data ? (
                <Suspense fallback={<p className="placeholder">{t('sources.viewer.loading')}</p>}>
                  <PdfView
                    {...view}
                    file={file.data.blob}
                    text={text.data ?? null}
                    page={page}
                    onPage={setPage}
                    onPageCount={setPageCount}
                    onError={() => setPdfFailed(true)}
                  />
                </Suspense>
              ) : file.error ? (
                <Alert>{errorMessage(file.error)}</Alert>
              ) : (
                <p className="placeholder">{t('sources.viewer.loading')}</p>
              )
            ) : text.data ? (
              <DomView {...view} text={text.data} html={text.data.html} />
            ) : text.error ? (
              <Alert>{errorMessage(text.error)}</Alert>
            ) : (
              <p className="placeholder">{t('sources.viewer.loading')}</p>
            )}
          </div>

          {showChunks && kind && !unsupported && (
            <ChunkList
              source={source.data}
              text={text.data}
              textError={text.error}
              active={target?.chunkId ?? null}
              onPick={(target) => setChosen(target)}
            />
          )}
        </div>
      </aside>
    </div>
  );
}

function Fallback({ message, onDownload }: { message: string; onDownload: () => void }) {
  const { t } = useI18n();
  return (
    <div className="viewer-fallback">
      <p>{message}</p>
      <button type="button" className="btn btn-primary" onClick={onDownload}>
        <DownloadIcon />
        {t('sources.viewer.download')}
      </button>
    </div>
  );
}

// Clamped to a few lines by CSS; no more is worth laying out.
const EXCERPT = 400;

/** What the AI reads of the file: its chunks, in order. */
function ChunkList({
  source,
  text,
  textError,
  active,
  onPick,
}: {
  source: Source | undefined;
  text: SourceText | undefined;
  textError: unknown;
  active: string | null;
  onPick: (target: Target) => void;
}) {
  const { t } = useI18n();
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (!active) return;
    list.current
      ?.querySelector(`[data-chunk="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [active, text]);

  let body: ReactNode;
  if (source && source.status !== 'ready') {
    body =
      source.status === 'failed' ? (
        <p className="hint">{codeMessage(source.error_code, 'sources.unknownFailure')}</p>
      ) : (
        <p className="hint">{t('sources.viewer.notReady')}</p>
      );
  } else if (textError) {
    body = <p className="hint">{errorMessage(textError)}</p>;
  } else if (!text) {
    body = <p className="placeholder">{t('sources.viewer.loading')}</p>;
  } else if (text.chunks.length === 0) {
    body = <p className="hint">{t('sources.viewer.noChunks')}</p>;
  } else {
    body = (
      <ol className="viewer-chunks" ref={list}>
        {text.chunks.map((chunk) => {
          const excerpt = text.text.slice(chunk.char_start, chunk.char_end);
          return (
            <li key={chunk.id}>
              <button
                type="button"
                className="viewer-chunk"
                data-chunk={chunk.id}
                aria-current={chunk.id === active ? 'true' : undefined}
                onClick={() => onPick(chunkTarget(text, chunk))}
              >
                <span className="viewer-chunk-meta">
                  {chunk.page_number !== null
                    ? t('sources.viewer.chunkOnPage', {
                        number: chunk.chunk_index + 1,
                        page: chunk.page_number,
                      })
                    : t('sources.viewer.chunk', { number: chunk.chunk_index + 1 })}
                </span>
                <span className="viewer-chunk-text">{excerpt.slice(0, EXCERPT)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <section className="viewer-side" aria-label={t('sources.viewer.chunksTitle')}>
      <h3 className="viewer-side-title">
        {t('sources.viewer.chunksTitle')}
        {text && <span className="badge">{text.chunks.length}</span>}
      </h3>
      <p className="hint">{t('sources.viewer.chunksHint')}</p>
      {body}
    </section>
  );
}
