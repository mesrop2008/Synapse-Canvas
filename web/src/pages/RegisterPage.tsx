import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';

import { register } from '../api/auth';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { FieldError, invalidProps } from '../components/FieldError';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { useField, validateAll } from '../hooks/useField';
import { useI18n } from '../hooks/useI18n';
import { checkEmail, checkNewPassword, checkRequired } from '../validation';
import type { VerifyEmailState } from './VerifyEmailPage';

export function RegisterPage() {
  const { status } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();

  const name = useField(checkRequired);
  const email = useField(checkEmail);
  const password = useField(checkNewPassword);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === 'authenticated') return <Navigate to="/workspaces" replace />;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!validateAll([[name, 'name'], [email, 'email'], [password, 'password']])) {
      return;
    }

    setSubmitting(true);
    try {
      const address = email.value.trim();
      await register(address, password.value, name.value);
      // 202 whether or not the address was new; the code page comes next either way.
      const state: VerifyEmailState = { email: address, sent: true };
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

      <form className="auth-card" onSubmit={handleSubmit} noValidate>
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
            value={name.value}
            onChange={(event) => name.setValue(event.target.value)}
            onBlur={name.touch}
            {...invalidProps('name', name.shown)}
          />
          <FieldError inputId="name" problem={name.shown} />
        </div>

        <div className="field">
          <label htmlFor="email">{t('register.email')}</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            placeholder={t('register.emailPlaceholder')}
            value={email.value}
            onChange={(event) => email.setValue(event.target.value)}
            onBlur={email.touch}
            {...invalidProps('email', email.shown)}
          />
          <FieldError inputId="email" problem={email.shown} />
        </div>

        <div className="field">
          <label htmlFor="password">{t('register.password')}</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder={t('register.passwordPlaceholder')}
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
          {submitting ? t('register.submitting') : t('register.submit')}
        </button>

        <p className="auth-foot">
          {t('register.haveAccount')} <Link to="/login">{t('register.signIn')}</Link>
        </p>
      </form>
    </main>
  );
}
