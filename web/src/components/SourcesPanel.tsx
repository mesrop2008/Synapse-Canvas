import { useRef, useState, type DragEvent } from 'react';

import { codeMessage, errorMessage } from '../api/errors';
import { useAIUsage } from '../hooks/useAIUsage';
import { useI18n } from '../hooks/useI18n';
import {
  useDeleteSource,
  useRetrySource,
  useSources,
  useUploads,
  type UploadState,
} from '../hooks/useSources';
import { formatBytes } from '../format';
import type { MessageKey } from '../../i18n';
import type { Source, SourceStatus, Workspace, WorkspaceUsage } from '../types/api';
import { canEdit } from '../types/api';
import { Alert } from './Alert';
import { ConfirmDialog } from './ConfirmDialog';
import { BookIcon, FileIcon, RetryIcon, TrashIcon, UploadIcon } from './icons';
import { useSourceViewer } from './sourceViewer/SourceViewer';

const ACCEPT = '.pdf,.docx,.txt,.md,.markdown';

const STATUS_LABELS: Record<SourceStatus, MessageKey> = {
  pending: 'sources.status.pending',
  processing: 'sources.status.processing',
  ready: 'sources.status.ready',
  failed: 'sources.status.failed',
};

function typeLabel(contentType: string): MessageKey {
  if (contentType === 'application/pdf') return 'sources.types.pdf';
  if (contentType.startsWith('application/vnd.openxmlformats')) return 'sources.types.docx';
  if (contentType === 'text/markdown') return 'sources.types.markdown';
  return 'sources.types.text';
}

interface Rejected {
  key: string;
  name: string;
}

export function SourcesPanel({ workspace }: { workspace: Workspace }) {
  const { t, tNode, locale } = useI18n();
  const sources = useSources(workspace.id);
  const usage = useAIUsage(workspace.id);
  const { uploads, start, dismiss } = useUploads(workspace.id);
  const remove = useDeleteSource(workspace.id);
  const retry = useRetrySource(workspace.id);
  const openViewer = useSourceViewer();

  const writable = canEdit(workspace.role);
  const [pendingDelete, setPendingDelete] = useState<Source | null>(null);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const maxBytes = usage.data?.source_max_file_bytes ?? 0;

  function accept(files: FileList | File[] | null) {
    const list = Array.from(files ?? []);
    // Checked here too, so a file the server will refuse is never sent.
    const tooLarge = maxBytes > 0 ? list.filter((file) => file.size > maxBytes) : [];
    if (tooLarge.length) {
      setRejected((current) => [
        ...current,
        ...tooLarge.map((file) => ({ key: `${file.name}-${Math.random()}`, name: file.name })),
      ]);
    }
    const fits = list.filter((file) => !tooLarge.includes(file));
    if (fits.length) start(fits);
  }

  return (
    <section className="sources page-section" aria-labelledby="sources-title">
      <div className="title-row">
        <h2 className="section-title" id="sources-title">
          {t('sources.title')}
        </h2>
        {sources.data && (
          <span className="badge">{t('sources.count', { count: sources.data.length })}</span>
        )}
      </div>
      <p className="hint">{t('sources.intro')}</p>

      <StorageMeter usage={usage.data} />

      {writable ? (
        <DropZone
          onFiles={accept}
          limit={maxBytes > 0 ? formatBytes(maxBytes, locale) : null}
        />
      ) : (
        <p className="hint">{t('sources.viewerNotice')}</p>
      )}

      {(uploads.length > 0 || rejected.length > 0) && (
        <ul className="upload-list">
          {rejected.map((item) => (
            <li key={item.key} className="upload" data-state="error">
              <span className="upload-name">{item.name}</span>
              <span className="upload-note">
                {t('sources.tooLargeHere', { size: formatBytes(maxBytes, locale) })}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() =>
                  setRejected((current) => current.filter((r) => r.key !== item.key))
                }
              >
                {t('sources.dismiss')}
              </button>
            </li>
          ))}
          {uploads.map((item) => (
            <UploadRow key={item.key} upload={item} onDismiss={() => dismiss(item.key)} />
          ))}
        </ul>
      )}

      {remove.error && (
        <Alert onDismiss={() => remove.reset()}>{errorMessage(remove.error)}</Alert>
      )}
      {retry.error && (
        <Alert onDismiss={() => retry.reset()}>{errorMessage(retry.error)}</Alert>
      )}
      {sources.error && <Alert>{errorMessage(sources.error)}</Alert>}
      {sources.isPending && <p className="placeholder">{t('sources.loading')}</p>}

      {sources.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <BookIcon size={22} />
          </div>
          <p className="empty-title">{t('sources.emptyTitle')}</p>
          <p className="empty-text">{t('sources.emptyText')}</p>
        </div>
      )}

      {sources.data && sources.data.length > 0 && (
        <ul className="source-list">
          {sources.data.map((source) => (
            <SourceRow
              key={source.id}
              source={source}
              writable={writable}
              retrying={retry.isPending && retry.variables === source.id}
              onRetry={() => retry.mutate(source.id)}
              onDelete={() => setPendingDelete(source)}
              onOpen={() =>
                openViewer({
                  workspaceId: workspace.id,
                  sourceId: source.id,
                  filename: source.filename,
                })
              }
            />
          ))}
        </ul>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={t('sources.deleteTitle')}
          message={tNode('sources.deleteMessage', {
            name: <strong>{pendingDelete.filename}</strong>,
          })}
          confirmLabel={t('common.delete')}
          destructive
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => {
            const target = pendingDelete;
            setPendingDelete(null);
            remove.mutate(target.id);
          }}
        />
      )}
    </section>
  );
}

function StorageMeter({ usage }: { usage: WorkspaceUsage | undefined }) {
  const { t, locale } = useI18n();
  if (!usage) return null;
  const used = formatBytes(usage.storage_used_bytes, locale);
  if (usage.storage_quota_bytes <= 0) {
    return <p className="storage muted">{t('sources.storage.unlimited', { used })}</p>;
  }
  const share = Math.min(1, usage.storage_used_bytes / usage.storage_quota_bytes);
  const label = t('sources.storage.used', {
    used,
    quota: formatBytes(usage.storage_quota_bytes, locale),
  });
  return (
    <div className="storage" data-low={share > 0.9 ? 'true' : undefined}>
      <div className="storage-row">
        <span>{t('sources.storage.label')}</span>
        <span className="muted">{label}</span>
      </div>
      <div
        className="ai-meter"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={usage.storage_quota_bytes}
        aria-valuenow={usage.storage_used_bytes}
      >
        <span style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function DropZone({
  onFiles,
  limit,
}: {
  onFiles: (files: FileList | null) => void;
  limit: string | null;
}) {
  const { t, tNode } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setActive(false);
    onFiles(event.dataTransfer.files);
  }

  return (
    <div
      className="dropzone"
      data-active={active ? 'true' : undefined}
      onDragOver={(event) => {
        event.preventDefault();
        setActive(true);
      }}
      onDragLeave={() => setActive(false)}
      onDrop={onDrop}
    >
      <UploadIcon size={22} />
      <p className="dropzone-text">
        {active
          ? t('sources.dropActive')
          : tNode('sources.drop', {
              choose: (
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => input.current?.click()}
                >
                  {t('sources.choose')}
                </button>
              ),
            })}
      </p>
      {limit && <p className="hint">{t('sources.accepted', { size: limit })}</p>}
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        aria-label={t('sources.dropLabel')}
        onChange={(event) => {
          onFiles(event.target.files);
          // Choosing the same file again still fires a change.
          event.target.value = '';
        }}
      />
    </div>
  );
}

function UploadRow({ upload, onDismiss }: { upload: UploadState; onDismiss: () => void }) {
  const { t } = useI18n();
  const percent = Math.round(upload.progress * 100);

  return (
    <li className="upload" data-state={upload.status}>
      <span className="upload-name">{upload.name}</span>
      {upload.status === 'uploading' && (
        <span
          className="progress"
          role="progressbar"
          aria-label={t('sources.progressLabel', { name: upload.name })}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <span style={{ width: `${percent}%` }} />
        </span>
      )}
      {upload.status === 'done' && <span className="upload-note">{t('sources.uploaded')}</span>}
      {upload.status === 'duplicate' && (
        <span className="upload-note">
          {t('sources.duplicate', { existing: upload.source?.filename ?? upload.name })}
        </span>
      )}
      {upload.status === 'error' && (
        <span className="upload-note">{errorMessage(upload.error)}</span>
      )}
      {upload.status !== 'uploading' && upload.status !== 'done' && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDismiss}>
          {t('sources.dismiss')}
        </button>
      )}
    </li>
  );
}

function SourceRow({
  source,
  writable,
  retrying,
  onRetry,
  onDelete,
  onOpen,
}: {
  source: Source;
  writable: boolean;
  retrying: boolean;
  onRetry: () => void;
  onDelete: () => void;
  onOpen: () => void;
}) {
  const { t, locale, formatRelative } = useI18n();
  const facts = [
    t(typeLabel(source.content_type)),
    formatBytes(source.size_bytes, locale),
    source.page_count !== null ? t('sources.pages', { count: source.page_count }) : null,
    source.status === 'ready' ? t('sources.chunks', { count: source.chunk_count }) : null,
    formatRelative(source.created_at),
  ].filter(Boolean);

  return (
    <li className="source" data-status={source.status}>
      <span className="row-icon">
        <FileIcon size={18} />
      </span>
      <div className="row-main">
        <button type="button" className="btn-link source-name" onClick={onOpen}>
          {source.filename}
        </button>
        <span className="meta">{facts.join(' · ')}</span>
        {source.status === 'failed' && (
          <p className="source-failure" role="status">
            {codeMessage(source.error_code, 'sources.unknownFailure')}{' '}
            <span className="muted">
              {t(source.retryable ? 'sources.retryLater' : 'sources.permanent')}
            </span>
          </p>
        )}
      </div>
      <span className="status-pill" data-status={source.status}>
        {(source.status === 'pending' || source.status === 'processing') && (
          <span className="spinner" aria-hidden="true" />
        )}
        {t(STATUS_LABELS[source.status])}
      </span>
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        title={t('sources.open', { name: source.filename })}
        aria-label={t('sources.open', { name: source.filename })}
        onClick={onOpen}
      >
        <BookIcon />
      </button>
      {writable && source.status === 'failed' && source.retryable && (
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={retrying}
          onClick={onRetry}
        >
          <RetryIcon size={14} />
          {t('sources.retry')}
        </button>
      )}
      {writable && (
        <button
          type="button"
          className="btn btn-ghost btn-danger btn-icon"
          title={t('sources.delete', { name: source.filename })}
          aria-label={t('sources.delete', { name: source.filename })}
          onClick={onDelete}
        >
          <TrashIcon />
        </button>
      )}
    </li>
  );
}
