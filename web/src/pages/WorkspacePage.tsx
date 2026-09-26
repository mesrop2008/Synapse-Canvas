import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { MemberPanel } from '../components/MemberPanel';
import {
  ChevronRightIcon,
  FileIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from '../components/icons';
import {
  useCreateDocument,
  useDeleteDocument,
  useDocuments,
} from '../hooks/useDocuments';
import { useI18n } from '../hooks/useI18n';
import { useRenameWorkspace, useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { MessageKey } from '../../i18n';
import type { DocumentSummary, WorkspaceRole } from '../types/api';

const ROLE_LABELS: Record<WorkspaceRole, MessageKey> = {
  owner: 'common.roles.owner',
  editor: 'common.roles.editor',
  viewer: 'common.roles.viewer',
};

export function WorkspacePage() {
  const { workspaceId = '' } = useParams();
  const { t, tNode, formatRelative } = useI18n();
  const workspace = useWorkspace(workspaceId);
  const documents = useDocuments(workspaceId);
  const create = useCreateDocument(workspaceId);
  const remove = useDeleteDocument(workspaceId);
  const rename = useRenameWorkspace(workspaceId);

  const [title, setTitle] = useState('');
  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);
  // Null unless a rename is in progress, so the heading is not an input the
  // rest of the time and cannot be edited by a stray click.
  const [draftName, setDraftName] = useState<string | null>(null);
  const current = workspace.data?.name ?? '';

  // The server enforces this; hiding the controls just avoids offering a viewer
  // a button that can only fail.
  const writable = workspace.data ? canEdit(workspace.data.role) : false;

  async function handleRename(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = (draftName ?? '').trim();
    if (!trimmed || trimmed === workspace.data?.name) {
      setDraftName(null);
      return;
    }
    try {
      await rename.mutateAsync(trimmed);
      setDraftName(null);
    } catch {
      // Surfaced from rename.error below; the draft stays so it is not lost.
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      await create.mutateAsync(trimmed);
      setTitle('');
    } catch {
      // Surfaced from create.error below.
    }
  }

  if (workspace.error) {
    return (
      <main className="container">
        <Alert>{errorMessage(workspace.error)}</Alert>
        <Link to="/workspaces">{t('workspace.backToWorkspaces')}</Link>
      </main>
    );
  }

  return (
    <main className="container">
      <nav className="crumbs">
        <Link to="/workspaces">{t('layout.workspaces')}</Link>
        <ChevronRightIcon size={14} />
        <span>{workspace.data?.name ?? '…'}</span>
      </nav>

      <div className="page-head">
        {draftName !== null ? (
          <form className="composer" onSubmit={handleRename}>
            <input
              className="input"
              autoFocus
              aria-label={t('workspace.renameLabel')}
              maxLength={255}
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setDraftName(null);
              }}
            />
            <button
              type="submit"
              className="btn btn-primary"
              disabled={rename.isPending || !draftName.trim()}
            >
              {rename.isPending ? t('workspace.renaming') : t('workspace.rename')}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setDraftName(null)}
            >
              {t('common.cancel')}
            </button>
          </form>
        ) : (
          <div className="title-row">
            <h1 className="page-title" style={{ marginBottom: 0 }}>
              {workspace.data?.name ?? t('common.loading')}
            </h1>
            {workspace.data && (
              <span className="badge badge-accent">
                {t(ROLE_LABELS[workspace.data.role])}
              </span>
            )}
            {workspace.data?.role === 'owner' && (
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                title={t('workspace.rename')}
                aria-label={t('workspace.rename')}
                onClick={() => setDraftName(current)}
              >
                <PencilIcon />
              </button>
            )}
          </div>
        )}
        {!writable && workspace.data && (
          <p className="page-sub" style={{ marginTop: 6 }}>
            {t('workspace.viewerNotice')}
          </p>
        )}
      </div>

      {writable && (
        <form className="composer" onSubmit={handleCreate}>
          <input
            className="input"
            type="text"
            aria-label={t('workspace.newDocumentLabel')}
            placeholder={t('workspace.newDocumentPlaceholder')}
            maxLength={255}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={create.isPending || !title.trim()}
          >
            <PlusIcon />
            {create.isPending ? t('workspace.creating') : t('workspace.newDocument')}
          </button>
        </form>
      )}

      {rename.error && (
        <Alert onDismiss={() => rename.reset()}>
          {errorMessage(rename.error, 'workspace.renameFailed')}
        </Alert>
      )}

      {(create.error || remove.error) && (
        <Alert
          onDismiss={() => {
            create.reset();
            remove.reset();
          }}
        >
          {errorMessage(create.error ?? remove.error)}
        </Alert>
      )}

      {documents.error && <Alert>{errorMessage(documents.error)}</Alert>}

      {documents.isPending && (
        <p className="placeholder">{t('workspace.loadingDocuments')}</p>
      )}

      {documents.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <FileIcon size={22} />
          </div>
          <p className="empty-title">{t('workspace.emptyTitle')}</p>
          <p className="empty-text">
            {writable
              ? t('workspace.emptyWritable')
              : t('workspace.emptyReadOnly')}
          </p>
        </div>
      )}

      {documents.data && documents.data.length > 0 && (
        <div className="card-grid">
          {documents.data.map((document) => (
            <div key={document.id} className="tile">
              <div className="tile-head">
                <span className="row-icon">
                  <FileIcon size={18} />
                </span>
                {writable && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-danger btn-icon row-action"
                    title={t('workspace.deleteDocument', { title: document.title })}
                    aria-label={t('workspace.deleteDocument', {
                      title: document.title,
                    })}
                    onClick={() => setPendingDelete(document)}
                    disabled={remove.isPending}
                  >
                    <TrashIcon />
                  </button>
                )}
              </div>

              <div className="row-main">
                <Link
                  className="row-title"
                  to={`/workspaces/${workspaceId}/documents/${document.id}`}
                >
                  {document.title}
                </Link>
              </div>

              <div className="tile-foot">
                <span className="meta">
                  {t('workspace.documentMeta', {
                    when: formatRelative(document.updated_at),
                    version: document.version,
                  })}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {workspace.data && <MemberPanel workspace={workspace.data} />}

      {pendingDelete && (
        <ConfirmDialog
          title={t('workspace.deleteTitle')}
          message={tNode('workspace.deleteMessage', {
            title: <strong>{pendingDelete.title}</strong>,
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
    </main>
  );
}
