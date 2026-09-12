import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import {
  ChevronRightIcon,
  FileIcon,
  PlusIcon,
  TrashIcon,
} from '../components/icons';
import {
  useCreateDocument,
  useDeleteDocument,
  useDocuments,
} from '../hooks/useDocuments';
import { useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { DocumentSummary } from '../types/api';

function formatEdited(timestamp: string): string {
  const edited = new Date(timestamp);
  const minutesAgo = (Date.now() - edited.getTime()) / 60_000;
  if (minutesAgo < 1) return 'just now';
  if (minutesAgo < 60) return `${Math.floor(minutesAgo)} min ago`;
  if (minutesAgo < 60 * 24) return `${Math.floor(minutesAgo / 60)} h ago`;
  return edited.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function WorkspacePage() {
  const { workspaceId = '' } = useParams();
  const workspace = useWorkspace(workspaceId);
  const documents = useDocuments(workspaceId);
  const create = useCreateDocument(workspaceId);
  const remove = useDeleteDocument(workspaceId);

  const [title, setTitle] = useState('');
  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);

  // The server enforces this; hiding the controls just avoids offering a viewer
  // a button that can only fail.
  const writable = workspace.data ? canEdit(workspace.data.role) : false;

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
        <Link to="/workspaces">Back to your workspaces</Link>
      </main>
    );
  }

  return (
    <main className="container">
      <nav className="crumbs">
        <Link to="/workspaces">Workspaces</Link>
        <ChevronRightIcon size={13} />
        <span>{workspace.data?.name ?? '…'}</span>
      </nav>

      <div className="page-head">
        <div className="title-row">
          <h1 className="page-title" style={{ marginBottom: 0 }}>
            {workspace.data?.name ?? 'Loading…'}
          </h1>
          {workspace.data && (
            <span className="badge badge-accent">{workspace.data.role}</span>
          )}
        </div>
        {!writable && workspace.data && (
          <p className="page-sub" style={{ marginTop: 6 }}>
            You have viewer access here, so documents are read-only.
          </p>
        )}
      </div>

      {writable && (
        <form className="composer" onSubmit={handleCreate}>
          <input
            className="input"
            type="text"
            aria-label="New document title"
            placeholder="Title a new document…"
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
            {create.isPending ? 'Creating…' : 'New document'}
          </button>
        </form>
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

      {documents.isPending && <p className="placeholder">Loading documents…</p>}

      {documents.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <FileIcon size={20} />
          </div>
          <p className="empty-title">No documents yet</p>
          <p className="empty-text">
            {writable
              ? 'Give one a title above and start writing.'
              : 'Nothing has been written in this workspace yet.'}
          </p>
        </div>
      )}

      {documents.data && documents.data.length > 0 && (
        <div className="panel">
          {documents.data.map((document) => (
            <div key={document.id} className="panel-row">
              <span className="row-icon">
                <FileIcon size={17} />
              </span>
              <div className="row-main">
                <Link
                  className="row-title"
                  to={`/workspaces/${workspaceId}/documents/${document.id}`}
                >
                  {document.title}
                </Link>
                <div className="meta">
                  Edited {formatEdited(document.updated_at)} · v{document.version}
                </div>
              </div>
              {writable && (
                <button
                  type="button"
                  className="btn btn-ghost btn-danger btn-icon row-action"
                  title={`Delete ${document.title}`}
                  aria-label={`Delete ${document.title}`}
                  onClick={() => setPendingDelete(document)}
                  disabled={remove.isPending}
                >
                  <TrashIcon />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this document?"
          message={
            <>
              <strong>{pendingDelete.title}</strong> and its contents will be
              removed. This cannot be undone.
            </>
          }
          confirmLabel="Delete"
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
