import { useState } from 'react';

import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { useI18n } from '../hooks/useI18n';
import {
  useAddMember,
  useMembers,
  useRemoveMember,
} from '../hooks/useWorkspaces';
import { ROLES } from '../types/api';
import { Alert } from './Alert';
import { ConfirmDialog } from './ConfirmDialog';
import { TrashIcon, UsersIcon } from './icons';
import type { MessageKey } from '../../i18n';
import type { Member, Workspace, WorkspaceRole } from '../types/api';

const ROLE_LABELS: Record<WorkspaceRole, MessageKey> = {
  owner: 'common.roles.owner',
  editor: 'common.roles.editor',
  viewer: 'common.roles.viewer',
};

/** First letter of each of the first two words; a one-word name still gets one. */
function initials(name: string, email: string): string {
  const source = name.trim() || email;
  const letters = source
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => [...word][0] ?? '')
    .join('');
  return letters.toUpperCase() || '?';
}

export function MemberPanel({ workspace }: { workspace: Workspace }) {
  const { t, tNode } = useI18n();
  const members = useMembers(workspace.id);
  const add = useAddMember(workspace.id);
  const remove = useRemoveMember(workspace.id);

  const [email, setEmail] = useState('');
  const [role, setRole] = useState<WorkspaceRole>('editor');
  const [formError, setFormError] = useState<FailedRequest | null>(null);
  const [pendingRemove, setPendingRemove] = useState<Member | null>(null);

  // Only the owner may change membership. The server enforces it; hiding the
  // controls avoids offering a button that can only fail.
  const administers = workspace.role === 'owner';

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    setFormError(null);
    try {
      await add.mutateAsync({ email: trimmed, role });
      setEmail('');
    } catch (caught) {
      setFormError(failed(caught, 'workspace.members.addFailed'));
    }
  }

  return (
    <section className="members">
      <div className="title-row">
        <h2 className="section-title">{t('workspace.members.title')}</h2>
        {members.data && (
          <span className="badge">
            {t('workspace.members.count', { count: members.data.length })}
          </span>
        )}
      </div>

      {administers && (
        <form className="composer composer-wide" onSubmit={handleAdd}>
          <input
            className="input"
            type="email"
            required
            aria-label={t('workspace.members.emailLabel')}
            placeholder={t('workspace.members.emailPlaceholder')}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <select
            className="input select"
            aria-label={t('workspace.members.roleLabel')}
            value={role}
            onChange={(event) =>
              setRole(event.target.value as WorkspaceRole)
            }
          >
            {ROLES.map((candidate) => (
              <option key={candidate} value={candidate}>
                {t(ROLE_LABELS[candidate])}
              </option>
            ))}
          </select>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={add.isPending || !email.trim()}
          >
            {add.isPending
              ? t('workspace.members.adding')
              : t('workspace.members.add')}
          </button>
        </form>
      )}

      {administers && (
        <p className="hint">{t('workspace.members.inviteHint')}</p>
      )}

      {formError && (
        <Alert onDismiss={() => setFormError(null)}>
          {describeFailure(formError)}
        </Alert>
      )}

      {remove.error && (
        <Alert onDismiss={() => remove.reset()}>
          {describeFailure(failed(remove.error, 'workspace.members.removeFailed'))}
        </Alert>
      )}

      {members.error && (
        <Alert>{describeFailure(failed(members.error))}</Alert>
      )}

      {members.isPending && (
        <p className="placeholder">{t('workspace.members.loading')}</p>
      )}

      {members.data && (
        <ul className="member-list">
          {members.data.map((member) => {
            // The workspace owner has no "remove" -- the server refuses it, and
            // an ownerless workspace could not be administered anyway.
            const isOwner = member.user_id === workspace.owner_id;
            return (
              <li key={member.id} className="member">
                <span className="avatar avatar-lg" aria-hidden="true">
                  {initials(member.user.name, member.user.email)}
                </span>
                <span className="row-main">
                  <span className="member-name">{member.user.name}</span>
                  <span className="meta">{member.user.email}</span>
                </span>
                <span className={`badge${isOwner ? ' badge-accent' : ''}`}>
                  {t(ROLE_LABELS[member.role])}
                </span>
                {administers && !isOwner && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-danger btn-icon"
                    title={t('workspace.members.remove', {
                      name: member.user.name,
                    })}
                    aria-label={t('workspace.members.remove', {
                      name: member.user.name,
                    })}
                    onClick={() => setPendingRemove(member)}
                    disabled={remove.isPending}
                  >
                    <TrashIcon />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {members.data?.length === 0 && (
        <div className="empty">
          <div className="empty-icon">
            <UsersIcon size={22} />
          </div>
          <p className="empty-title">{t('workspace.members.emptyTitle')}</p>
        </div>
      )}

      {pendingRemove && (
        <ConfirmDialog
          title={t('workspace.members.removeTitle')}
          message={tNode('workspace.members.removeMessage', {
            name: <strong>{pendingRemove.user.name}</strong>,
          })}
          confirmLabel={t('workspace.members.removeConfirm')}
          destructive
          onCancel={() => setPendingRemove(null)}
          onConfirm={() => {
            const target = pendingRemove;
            setPendingRemove(null);
            remove.mutate(target.user_id);
          }}
        />
      )}
    </section>
  );
}
