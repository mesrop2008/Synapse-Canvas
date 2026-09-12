import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import {
  useCreateDocument,
  useDeleteDocument,
  useDocuments,
} from '../hooks/useDocuments';
import { useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { DocumentSummary } from '../types/api';

function formatEdited(timestamp: string): string {
  return new Date(timestamp).toLocaleString();
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
      <main className="page">
        <Alert>{errorMessage(workspace.error)}</Alert>
        <Link to="/workspaces">Back to your workspaces</Link>
      </main>
    );
  }

  return (
    <main className="page">
      <p className="breadcrumb">
        <Link to="/workspaces">Workspaces</Link> / {workspace.data?.name ?? '…'}
      </p>

      <div className="row">
        <h1>{workspace.data?.name ?? 'Loading…'}</h1>
        {workspace.data && <span className="badge">{workspace.data.role}</span>}
      </div>

      {writable && (
        <form className="card inline-form" onSubmit={handleCreate}>
          <input
            type="text"
            aria-label="New document title"
            placeholder="New document title"
            maxLength={255}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <button
            type="submit"
            className="primary"
            disabled={create.isPending || !title.trim()}
          >
            {create.isPending ? 'Creating…' : 'New document'}
          </button>
        </form>
      )}

      {!writable && workspace.data && (
        <p className="subtle">
          You have viewer access here, so documents are read-only.
        </p>
      )}

      {(create.error || remove.error) && (
        <div style={{ marginTop: '0.75rem' }}>
          <Alert
            onDismiss={() => {
              create.reset();
              remove.reset();
            }}
          >
            {errorMessage(create.error ?? remove.error)}
          </Alert>
        </div>
      )}

      {documents.isPending && <p className="page-placeholder">Loading documents…</p>}

      {documents.error && (
        <div style={{ marginTop: '0.75rem' }}>
          <Alert>{errorMessage(documents.error)}</Alert>
        </div>
      )}

      {documents.data?.length === 0 && (
        <p className="empty">
          {writable
            ? 'No documents yet. Create one above.'
            : 'No documents in this workspace yet.'}
        </p>
      )}

      {documents.data && documents.data.length > 0 && (
        <ul className="list">
          {documents.data.map((document) => (
            <li key={document.id} className="list-item">
              <div>
                <Link
                  className="title"
                  to={`/workspaces/${workspaceId}/documents/${document.id}`}
                >
                  {document.title}
                </Link>
                <div className="subtle">
                  Edited {formatEdited(document.updated_at)} · v{document.version}
                </div>
              </div>
              {writable && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => setPendingDelete(document)}
                  disabled={remove.isPending}
                >
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
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
