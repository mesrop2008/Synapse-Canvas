import { useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { FolderIcon, PlusIcon } from '../components/icons';
import { useCreateWorkspace, useWorkspaces } from '../hooks/useWorkspaces';

export function WorkspacesPage() {
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
        <h1 className="page-title">Workspaces</h1>
        <p className="page-sub">
          Every document lives in a workspace, and your role there decides what you
          can do with it.
        </p>
      </div>

      <form className="composer" onSubmit={handleCreate}>
        <input
          className="input"
          type="text"
          aria-label="New workspace name"
          placeholder="Name a new workspace…"
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
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </form>

      {create.error && (
        <Alert onDismiss={() => create.reset()}>
          {errorMessage(create.error, 'Could not create the workspace.')}
        </Alert>
      )}

      {workspaces.error && <Alert>{errorMessage(workspaces.error)}</Alert>}

      {workspaces.isPending && <p className="placeholder">Loading…</p>}

      {workspaces.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <FolderIcon size={20} />
          </div>
          <p className="empty-title">No workspaces yet</p>
          <p className="empty-text">Name one above to get going.</p>
        </div>
      )}

      {workspaces.data && workspaces.data.length > 0 && (
        <div className="panel">
          {workspaces.data.map((workspace) => (
            <div key={workspace.id} className="panel-row">
              <span className="row-icon">
                <FolderIcon size={17} />
              </span>
              <div className="row-main">
                <Link className="row-title" to={`/workspaces/${workspace.id}`}>
                  {workspace.name}
                </Link>
              </div>
              <span className="badge">{workspace.role}</span>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
