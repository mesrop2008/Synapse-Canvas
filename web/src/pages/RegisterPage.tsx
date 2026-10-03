import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';

import { register } from '../api/auth';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';
import type { VerifyEmailState } from './VerifyEmailPage';

export function RegisterPage() {
  const { status } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<FailedRequest | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === 'authenticated') return <Navigate to="/workspaces" replace />;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password, name);
      // The backend answers 202 either way and the account stays inactive
      // until the emailed code comes back, so the next stop is entering it.
      const state: VerifyEmailState = { email, sent: true };
      navigate('/verify-email', { state });
    } catch (caught) {
      setError(failed(caught, 'register.failed'));
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

        <h1 className="auth-title">{t('register.title')}</h1>
        <p className="auth-lede">{t('register.lede')}</p>

        {error && (
          <Alert onDismiss={() => setError(null)}>
            {describeFailure(error)}
          </Alert>
        )}

        <div className="field">
          <label htmlFor="name">{t('register.name')}</label>
          <input
            id="name"
            className="input"
            type="text"
            autoComplete="name"
            placeholder={t('register.namePlaceholder')}
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="email">{t('register.email')}</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            placeholder={t('register.emailPlaceholder')}
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">{t('register.password')}</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder={t('register.passwordPlaceholder')}
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        <button
          type="submit"
          className="btn btn-primary btn-block"
          disabled={submitting}
        >
          {submitting ? t('register.submitting') : t('register.submit')}
        </button>

        <p className="auth-foot">
          {t('register.haveAccount')} <Link to="/login">{t('register.signIn')}</Link>
        </p>
      </form>
    </main>
  );
}
