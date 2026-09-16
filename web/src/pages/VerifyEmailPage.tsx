import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { verifyEmail } from '../api/auth';
import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useI18n } from '../hooks/useI18n';

type State = 'missing' | 'verifying' | 'verified' | 'failed';

/** EMAIL_VERIFICATION_LINK_BASE has to point here, or the token never reaches
 *  /auth/verify-email. */
export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const { t } = useI18n();
  const token = params.get('token');

  const [state, setState] = useState<State>(token ? 'verifying' : 'missing');
  const [error, setError] = useState<string | null>(null);
  // The token is single-use and StrictMode runs this effect twice in dev.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    verifyEmail(token)
      .then(() => setState('verified'))
      .catch((caught: unknown) => {
        setError(errorMessage(caught, 'verifyEmail.failed'));
        setState('failed');
      });
  }, [token]);

  return (
    <main className="auth">
      <div className="auth-theme">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      <div className="auth-card">
        <div className="auth-brand">
          <Logo size={26} />
          {t('common.appName')}
        </div>

        <h1 className="auth-title">{t('verifyEmail.title')}</h1>

        {state === 'missing' && (
          <p className="auth-lede">{t('verifyEmail.missing')}</p>
        )}

        {state === 'verifying' && (
          <p className="auth-lede">{t('verifyEmail.checking')}</p>
        )}

        {state === 'verified' && (
          <p className="auth-lede">{t('verifyEmail.verified')}</p>
        )}

        {state === 'failed' && (
          <>
            <Alert>{error}</Alert>
            <p className="note">{t('verifyEmail.expiredNote')}</p>
          </>
        )}

        <p className="auth-foot">
          <Link to="/login">{t('verifyEmail.goToSignIn')}</Link>
        </p>
      </div>
    </main>
  );
}
