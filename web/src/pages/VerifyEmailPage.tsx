import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { resendVerification, verifyEmail } from '../api/auth';
import { ApiError } from '../api/client';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useI18n } from '../hooks/useI18n';

/** Mirrors EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS. Only a hint for the
 *  button: the server enforces the real one, and its Retry-After wins. */
const RESEND_COOLDOWN_SECONDS = 60;

/** How the register, login and account pages hand over the address. In router
 *  state rather than the query string, so it stays out of history and logs. */
export interface VerifyEmailState {
  email?: string;
  /** True when a code was sent on the way here, so the cooldown is running. */
  sent?: boolean;
}

export function VerifyEmailPage() {
  const { t, tNode } = useI18n();
  const handedOver = (useLocation().state as VerifyEmailState | null) ?? {};

  const [email, setEmail] = useState(handedOver.email ?? '');
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [resent, setResent] = useState(false);
  const [cooldown, setCooldown] = useState(
    handedOver.sent ? RESEND_COOLDOWN_SECONDS : 0,
  );

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((left) => left - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setResent(false);
    setSubmitting(true);
    try {
      await verifyEmail(email, code);
      setVerified(true);
    } catch (caught) {
      setError(failed(caught, 'verifyEmail.failed'));
      setCode('');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResend() {
    setError(null);
    setResent(false);
    try {
      await resendVerification(email);
      setResent(true);
      setCode('');
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 429) {
        setCooldown(caught.retryAfter ?? RESEND_COOLDOWN_SECONDS);
      }
      setError(failed(caught, 'verifyEmail.resendFailed'));
    }
  }

  return (
    <main className="auth">
      <div className="auth-theme">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      {verified ? (
        <div className="auth-card">
          <div className="auth-brand">
            <Logo size={26} />
            {t('common.appName')}
          </div>
          <h1 className="auth-title">{t('verifyEmail.verifiedTitle')}</h1>
          <p className="auth-lede">{t('verifyEmail.verified')}</p>
          <Link to="/login" className="btn btn-primary btn-block">
            {t('verifyEmail.goToSignIn')}
          </Link>
        </div>
      ) : (
        <form className="auth-card" onSubmit={handleSubmit}>
          <div className="auth-brand">
            <Logo size={26} />
            {t('common.appName')}
          </div>

          <h1 className="auth-title">{t('verifyEmail.title')}</h1>
          <p className="auth-lede">
            {handedOver.email
              ? tNode('verifyEmail.ledeKnown', {
                  email: <strong>{handedOver.email}</strong>,
                })
              : t('verifyEmail.lede')}
          </p>

          {error && (
            <Alert onDismiss={() => setError(null)}>{describeFailure(error)}</Alert>
          )}
          {resent && (
            <Alert kind="warn" onDismiss={() => setResent(false)}>
              {t('verifyEmail.resent')}
            </Alert>
          )}

          {!handedOver.email && (
            <div className="field">
              <label htmlFor="email">{t('verifyEmail.email')}</label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="username"
                placeholder={t('verifyEmail.emailPlaceholder')}
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
          )}

          <div className="field">
            <label htmlFor="code">{t('verifyEmail.code')}</label>
            <input
              id="code"
              className="input input-code"
              type="text"
              inputMode="numeric"
              // Lets iOS and Android offer the code straight from the email.
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              // No maxLength: it would cut a pasted "123 456" to "123 45"
              // before the filter below could drop the space.
              placeholder="000000"
              required
              autoFocus={Boolean(handedOver.email)}
              value={code}
              // Pasting "123 456" or "123-456" should still work.
              onChange={(event) =>
                setCode(event.target.value.replace(/\D/g, '').slice(0, 6))
              }
            />
            <p className="hint">{t('verifyEmail.codeHint')}</p>
          </div>

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submitting || code.length !== 6}
          >
            {submitting ? t('verifyEmail.submitting') : t('verifyEmail.submit')}
          </button>

          <div style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn btn-secondary btn-block"
              disabled={cooldown > 0 || !email}
              onClick={() => void handleResend()}
            >
              {cooldown > 0
                ? t('verifyEmail.resendIn', { seconds: cooldown })
                : t('verifyEmail.resend')}
            </button>
          </div>

          <p className="auth-foot">
            <Link to="/login">{t('verifyEmail.backToSignIn')}</Link>
          </p>
        </form>
      )}
    </main>
  );
}
