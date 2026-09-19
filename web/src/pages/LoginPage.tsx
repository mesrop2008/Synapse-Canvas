import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';

import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';

export function LoginPage() {
  const { status, signIn } = useAuth();
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
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
    setSubmitting(true);
    try {
      await signIn(email, password);
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

      <form className="auth-card" onSubmit={handleSubmit}>
        <div className="auth-brand">
          <Logo size={26} />
          {t('common.appName')}
        </div>

        <h1 className="auth-title">{t('login.title')}</h1>
        <p className="auth-lede">{t('login.lede')}</p>

        {error && (
          <Alert onDismiss={() => setError(null)}>
            {describeFailure(error)}
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
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">{t('login.password')}</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            placeholder={t('login.passwordPlaceholder')}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
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
