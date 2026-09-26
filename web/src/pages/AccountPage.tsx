import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { logoutEverywhere, resendVerification } from '../api/auth';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';

type Notice = 'verificationSent' | null;

export function AccountPage() {
  const { t, formatRelative } = useI18n();
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (!user) return <p className="placeholder">{t('common.loading')}</p>;

  const { email } = user;

  async function handleResend() {
    setBusy(true);
    setError(null);
    try {
      await resendVerification(email);
      setNotice('verificationSent');
    } catch (caught) {
      setError(failed(caught, 'account.resendFailed'));
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOutEverywhere() {
    setBusy(true);
    setError(null);
    try {
      await logoutEverywhere();
      // Every refresh token is revoked, this tab's included, so the only
      // honest next step is the login page.
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

      {notice === 'verificationSent' && (
        <Alert kind="warn" onDismiss={() => setNotice(null)}>
          {t('account.resendSent')}
        </Alert>
      )}

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
            {user.email_verified_at ? (
              <span className="badge badge-accent">
                {t('account.verifiedYes')}
              </span>
            ) : (
              <>
                <span className="badge">{t('account.verifiedNo')}</span>{' '}
                <button
                  type="button"
                  className="btn-link"
                  onClick={handleResend}
                  disabled={busy}
                >
                  {t('account.resend')}
                </button>
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
