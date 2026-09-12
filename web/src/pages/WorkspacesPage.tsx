import { useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
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
    <main className="page">
      <h1>Workspaces</h1>
      <p className="subtle">
        Every document lives in a workspace, and your role there decides what you
        can do with it.
      </p>

      <form className="card inline-form" onSubmit={handleCreate}>
        <input
          type="text"
          aria-label="New workspace name"
          placeholder="New workspace name"
          maxLength={255}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button
          type="submit"
          className="primary"
          disabled={create.isPending || !name.trim()}
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </form>

      {create.error && (
        <div style={{ marginTop: '0.75rem' }}>
          <Alert onDismiss={() => create.reset()}>
            {errorMessage(create.error, 'Could not create the workspace.')}
          </Alert>
        </div>
      )}

      {workspaces.isPending && <p className="page-placeholder">Loading…</p>}

      {workspaces.error && (
        <div style={{ marginTop: '0.75rem' }}>
          <Alert>{errorMessage(workspaces.error)}</Alert>
        </div>
      )}

      {workspaces.data?.length === 0 && (
        <p className="empty">No workspaces yet. Create one above to get going.</p>
      )}

      {workspaces.data && workspaces.data.length > 0 && (
        <ul className="list">
          {workspaces.data.map((workspace) => (
            <li key={workspace.id} className="list-item">
              <Link className="title" to={`/workspaces/${workspace.id}`}>
                {workspace.name}
              </Link>
              <span className="badge">{workspace.role}</span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
