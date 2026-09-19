import { useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { FolderIcon, PlusIcon, TrashIcon } from '../components/icons';
import { useI18n } from '../hooks/useI18n';
import {
  useCreateWorkspace,
  useDeleteWorkspace,
  useWorkspaces,
} from '../hooks/useWorkspaces';
import type { MessageKey } from '../../i18n';
import type { Workspace, WorkspaceRole } from '../types/api';

const ROLE_LABELS: Record<WorkspaceRole, MessageKey> = {
  owner: 'common.roles.owner',
  editor: 'common.roles.editor',
  viewer: 'common.roles.viewer',
};

export function WorkspacesPage() {
  const { t, tNode } = useI18n();
  const workspaces = useWorkspaces();
  const create = useCreateWorkspace();
  const remove = useDeleteWorkspace();
  const [name, setName] = useState('');
  const [pendingDelete, setPendingDelete] = useState<Workspace | null>(null);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await create.mutateAsync(trimmed);
      setName('');
    } catch {
      // Surfaced from create.error below.
    }
  }

  return (
    <main className="container">
      <div className="page-head">
        <h1 className="page-title">{t('workspaces.title')}</h1>
        <p className="page-sub">{t('workspaces.lede')}</p>
      </div>

      <form className="composer" onSubmit={handleCreate}>
        <input
          className="input"
          type="text"
          aria-label={t('workspaces.newLabel')}
          placeholder={t('workspaces.newPlaceholder')}
          maxLength={255}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={create.isPending || !name.trim()}
        >
          <PlusIcon />
          {create.isPending ? t('workspaces.creating') : t('workspaces.create')}
        </button>
      </form>

      {(create.error || remove.error) && (
        <Alert
          onDismiss={() => {
            create.reset();
            remove.reset();
          }}
        >
          {create.error
            ? errorMessage(create.error, 'workspaces.createFailed')
            : errorMessage(remove.error, 'workspaces.deleteFailed')}
        </Alert>
      )}

      {workspaces.error && <Alert>{errorMessage(workspaces.error)}</Alert>}

      {workspaces.isPending && <p className="placeholder">{t('common.loading')}</p>}

      {workspaces.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <FolderIcon size={22} />
          </div>
          <p className="empty-title">{t('workspaces.emptyTitle')}</p>
          <p className="empty-text">{t('workspaces.emptyText')}</p>
        </div>
      )}

      {workspaces.data && workspaces.data.length > 0 && (
        <div className="card-grid">
          {workspaces.data.map((workspace) => (
            <div key={workspace.id} className="tile">
              <div className="tile-head">
                <span className="row-icon">
                  <FolderIcon size={18} />
                </span>
                {/* Owners only. The server enforces it either way; hiding the
                    button just avoids offering one that can only fail. */}
                {workspace.role === 'owner' && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-danger btn-icon row-action"
                    title={t('workspaces.delete', { name: workspace.name })}
                    aria-label={t('workspaces.delete', { name: workspace.name })}
                    onClick={() => setPendingDelete(workspace)}
                    disabled={remove.isPending}
                  >
                    <TrashIcon />
                  </button>
                )}
              </div>
              <div className="row-main">
                <Link className="row-title" to={`/workspaces/${workspace.id}`}>
                  {workspace.name}
                </Link>
              </div>
              <div className="tile-foot">
                <span className="badge">{t(ROLE_LABELS[workspace.role])}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={t('workspaces.deleteTitle')}
          message={tNode('workspaces.deleteMessage', {
            name: <strong>{pendingDelete.name}</strong>,
          })}
          confirmLabel={t('common.delete')}
          confirmPhrase={pendingDelete.name}
          confirmPhraseLabel={t('workspaces.deleteConfirmLabel', {
            name: pendingDelete.name,
          })}
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
