import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { logoutEverywhere } from '../api/auth';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';
import type { VerifyEmailState } from './VerifyEmailPage';

export function AccountPage() {
  const { t, formatRelative } = useI18n();
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (!user) return <p className="placeholder">{t('common.loading')}</p>;

  async function handleSignOutEverywhere() {
    setBusy(true);
    setError(null);
    try {
      await logoutEverywhere();
      await signOut();
      navigate('/login', { replace: true });
    } catch (caught) {
      setError(failed(caught, 'account.signOutEverywhereFailed'));
      setBusy(false);
    }
  }

  return (
    <main className="container container-reading">
      <div className="page-head">
        <h1 className="page-title">{t('account.title')}</h1>
        <p className="page-sub">{t('account.lede')}</p>
      </div>

      {error && (
        <Alert onDismiss={() => setError(null)}>{describeFailure(error)}</Alert>
      )}

      <dl className="detail-list">
        <div>
          <dt>{t('account.name')}</dt>
          <dd>{user.name}</dd>
        </div>
        <div>
          <dt>{t('account.email')}</dt>
          <dd>{user.email}</dd>
        </div>
        <div>
          <dt>{t('account.since')}</dt>
          <dd>{formatRelative(user.created_at)}</dd>
        </div>
        <div>
          <dt>{t('account.verified')}</dt>
          <dd>
            {user.is_active ? (
              <span className="badge badge-accent">
                {t('account.verifiedYes')}
              </span>
            ) : (
              <>
                <span className="badge">{t('account.verifiedNo')}</span>{' '}
                <Link
                  to="/verify-email"
                  state={{ email: user.email } satisfies VerifyEmailState}
                  className="btn-link"
                >
                  {t('account.resend')}
                </Link>
              </>
            )}
          </dd>
        </div>
      </dl>

      <section className="page-section">
        <h2 className="section-title">{t('account.sessionsTitle')}</h2>
        <p className="hint">{t('account.sessionsHint')}</p>
        <button
          type="button"
          className="btn btn-secondary btn-danger"
          onClick={() => setConfirming(true)}
          disabled={busy}
        >
          {t('account.signOutEverywhere')}
        </button>
      </section>

      {confirming && (
        <ConfirmDialog
          title={t('account.signOutEverywhereTitle')}
          message={t('account.signOutEverywhereMessage')}
          confirmLabel={t('account.signOutEverywhere')}
          destructive
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            void handleSignOutEverywhere();
          }}
        />
      )}
    </main>
  );
}
