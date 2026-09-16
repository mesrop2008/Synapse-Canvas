import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';

import { register, resendVerification } from '../api/auth';
import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';

export function RegisterPage() {
  const { status } = useAuth();
  const { t, tNode } = useI18n();

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [resent, setResent] = useState(false);

  if (status === 'authenticated') return <Navigate to="/workspaces" replace />;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password, name);
      setSubmitted(true);
    } catch (caught) {
      setError(errorMessage(caught, 'register.failed'));
    } finally {
      setSubmitting(false);
    }
  }

  // The backend answers 202 either way and refuses login until verified, so
  // there is nobody to sign in here -- only somewhere to send them.
  if (submitted) {
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

          <h1 className="auth-title">{t('register.sentTitle')}</h1>
          <p className="auth-lede">
            {tNode('register.sentLede', { email: <strong>{email}</strong> })}
          </p>

          <p className="note">
            {tNode('register.sentDevNote', {
              marker: <code>[email:console]</code>,
            })}
          </p>

          <div style={{ marginTop: 18 }}>
            <button
              type="button"
              className="btn btn-secondary btn-block"
              disabled={resent}
              onClick={() => {
                void resendVerification(email).catch(() => undefined);
                setResent(true);
              }}
            >
              {resent ? t('register.resent') : t('register.resend')}
            </button>
          </div>

          <p className="auth-foot">
            <Link to="/login">{t('register.backToSignIn')}</Link>
          </p>
        </div>
      </main>
    );
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

        {error && <Alert onDismiss={() => setError(null)}>{error}</Alert>}

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
