import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '../api/client';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { FieldError, invalidProps } from '../components/FieldError';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { useField, validateAll } from '../hooks/useField';
import { useI18n } from '../hooks/useI18n';
import { checkEmail, checkRequired } from '../validation';
import type { VerifyEmailState } from './VerifyEmailPage';

export function LoginPage() {
  const { status, signIn } = useAuth();
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();

  const email = useField(checkEmail);
  const password = useField(checkRequired);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Set by ProtectedRoute when it intercepted a deep link.
  const from = (location.state as { from?: string } | null)?.from ?? '/workspaces';

  if (status === 'loading') {
    return <p className="placeholder">{t('layout.restoringSession')}</p>;
  }
  if (status === 'authenticated') {
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!validateAll([[email, 'email'], [password, 'password']])) return;

    setSubmitting(true);
    try {
      await signIn(email.value.trim(), password.value);
      navigate(from, { replace: true });
    } catch (caught) {
      setError(failed(caught, 'login.failed'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth">
      <div className="auth-theme">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      <form className="auth-card" onSubmit={handleSubmit} noValidate>
        <div className="auth-brand">
          <Logo size={26} />
          {t('common.appName')}
        </div>

        <h1 className="auth-title">{t('login.title')}</h1>
        <p className="auth-lede">{t('login.lede')}</p>

        {error && (
          <Alert onDismiss={() => setError(null)}>
            {describeFailure(error)}
            {error.cause instanceof ApiError &&
              error.cause.code === 'auth.email_unverified' && (
                <>
                  {' '}
                  <Link
                    to="/verify-email"
                    state={{ email: email.value.trim() } satisfies VerifyEmailState}
                    className="btn-link"
                  >
                    {t('login.enterCode')}
                  </Link>
                </>
              )}
          </Alert>
        )}

        <div className="field">
          <label htmlFor="email">{t('login.email')}</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            placeholder={t('login.emailPlaceholder')}
            value={email.value}
            onChange={(event) => email.setValue(event.target.value)}
            onBlur={email.touch}
            {...invalidProps('email', email.shown)}
          />
          <FieldError inputId="email" problem={email.shown} />
        </div>

        <div className="field">
          <label htmlFor="password">{t('login.password')}</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            placeholder={t('login.passwordPlaceholder')}
            value={password.value}
            onChange={(event) => password.setValue(event.target.value)}
            onBlur={password.touch}
            {...invalidProps('password', password.shown)}
          />
          <FieldError inputId="password" problem={password.shown} />
        </div>

        <button
          type="submit"
          className="btn btn-primary btn-block"
          disabled={submitting}
        >
          {submitting ? t('login.submitting') : t('login.submit')}
        </button>

        <p className="auth-foot">
          {t('login.noAccount')} <Link to="/register">{t('login.createOne')}</Link>
        </p>
      </form>
    </main>
  );
}
