import { useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { FolderIcon, PlusIcon } from '../components/icons';
import { useI18n } from '../hooks/useI18n';
import { useCreateWorkspace, useWorkspaces } from '../hooks/useWorkspaces';
import type { MessageKey } from '../../i18n';
import type { WorkspaceRole } from '../types/api';

const ROLE_LABELS: Record<WorkspaceRole, MessageKey> = {
  owner: 'common.roles.owner',
  editor: 'common.roles.editor',
  viewer: 'common.roles.viewer',
};

export function WorkspacesPage() {
  const { t } = useI18n();
  const workspaces = useWorkspaces();
  const create = useCreateWorkspace();
  const [name, setName] = useState('');

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

      {create.error && (
        <Alert onDismiss={() => create.reset()}>
          {errorMessage(create.error, 'workspaces.createFailed')}
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

    </main>
  );
}
