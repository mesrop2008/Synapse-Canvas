import { useRef, useState, type ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';

import type { MessageKey } from '../../i18n';
import {
  requestPasswordReset,
  resetPassword,
  verifyPasswordResetCode,
} from '../api/auth';
import { ApiError } from '../api/client';
import { describeFailure, failed, type FailedRequest } from '../api/errors';
import { Alert } from '../components/Alert';
import { CodeInput } from '../components/CodeInput';
import { CountdownRing } from '../components/CountdownRing';
import { FieldError, invalidProps } from '../components/FieldError';
import { LanguageToggle } from '../components/LanguageToggle';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';
import { formatCountdown, useCountdown } from '../hooks/useCountdown';
import { useField, validateAll } from '../hooks/useField';
import { useI18n } from '../hooks/useI18n';
import { checkEmail, checkNewPassword } from '../validation';

/** Hints mirroring the server's settings; its Retry-After and errors win. */
const RESEND_COOLDOWN_SECONDS = 60;
const CODE_TTL_MINUTES = 5;
const MAX_ATTEMPTS = 3;

const TOTAL_STEPS = 3;

/** In router state, not the query string, to keep the address out of logs. */
export interface ForgotPasswordState {
  email?: string;
}

type Step =
  | { name: 'request' }
  | { name: 'verify'; email: string }
  | { name: 'reset'; email: string; resetToken: string }
  | { name: 'done' };

export function ForgotPasswordPage() {
  const { status } = useAuth();
  const { t } = useI18n();
  const handedOver = (useLocation().state as ForgotPasswordState | null) ?? {};

  const [step, setStep] = useState<Step>({ name: 'request' });
  const [lastEmail, setLastEmail] = useState(handedOver.email ?? '');

  if (status === 'loading') {
    return <p className="placeholder">{t('layout.restoringSession')}</p>;
  }
  if (status === 'authenticated') {
    return <Navigate to="/workspaces" replace />;
  }

  const restart = () => setStep({ name: 'request' });

  return (
    <main className="auth">
      <div className="auth-theme">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      {step.name === 'request' && (
        <RequestStep
          initialEmail={lastEmail}
          onSent={(email) => {
            setLastEmail(email);
            setStep({ name: 'verify', email });
          }}
        />
      )}
      {step.name === 'verify' && (
        <VerifyStep
          email={step.email}
          onVerified={(resetToken) =>
            setStep({ name: 'reset', email: step.email, resetToken })
          }
          onRestart={restart}
        />
      )}
      {step.name === 'reset' && (
        <ResetStep
          email={step.email}
          resetToken={step.resetToken}
          onDone={() => setStep({ name: 'done' })}
          onRestart={restart}
        />
      )}
      {step.name === 'done' && (
        <div className="auth-card">
          <CardHead title={t('forgotPassword.doneTitle')} lede={t('forgotPassword.done')} />
          <Link to="/login" className="btn btn-primary btn-block">
            {t('forgotPassword.goToSignIn')}
          </Link>
        </div>
      )}
    </main>
  );
}

function CardHead({
  step,
  title,
  lede,
}: {
  step?: number;
  title: string;
  lede: ReactNode;
}) {
  const { t } = useI18n();
  const label = step ? t('forgotPassword.step', { current: step, total: TOTAL_STEPS }) : '';

  return (
    <>
      <div className="auth-brand">
        <Logo size={26} />
        {t('common.appName')}
      </div>
      {step && (
        <div
          className="auth-steps"
          role="progressbar"
          aria-valuemin={1}
          aria-valuemax={TOTAL_STEPS}
          aria-valuenow={step}
          aria-valuetext={label}
        >
          <span className="auth-steps-bars" aria-hidden="true">
            {Array.from({ length: TOTAL_STEPS }, (_, index) => (
              <span key={index} data-reached={index < step || undefined} />
            ))}
          </span>
          <span aria-hidden="true">{label}</span>
        </div>
      )}
      <h1 className="auth-title">{title}</h1>
      <p className="auth-lede">{lede}</p>
    </>
  );
}

function Wait({
  secondsLeft,
  totalSeconds,
  message,
  tone,
}: {
  secondsLeft: number;
  totalSeconds: number;
  message: MessageKey;
  tone?: 'warn';
}) {
  const { t } = useI18n();
  const time = formatCountdown(secondsLeft);

  return (
    <div className="wait" data-tone={tone}>
      <CountdownRing
        secondsLeft={secondsLeft}
        totalSeconds={totalSeconds}
        label={t('forgotPassword.timeLeft', { time })}
      />
      <p className="wait-text">{t(message, { time })}</p>
    </div>
  );
}

function isTooSoon(caught: unknown): caught is ApiError & { retryAfter: number } {
  return caught instanceof ApiError && caught.status === 429 && caught.retryAfter !== null;
}

function RequestStep({
  initialEmail,
  onSent,
}: {
  initialEmail: string;
  onSent: (email: string) => void;
}) {
  const { t } = useI18n();
  const email = useField(checkEmail, initialEmail);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<FailedRequest | null>(null);
  const [wait, startWait, waitTotal] = useCountdown();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!validateAll([[email, 'email']])) return;

    setSubmitting(true);
    const address = email.value.trim();
    try {
      await requestPasswordReset(address);
      onSent(address);
    } catch (caught) {
      if (isTooSoon(caught)) startWait(caught.retryAfter);
      setError(failed(caught, 'forgotPassword.requestFailed'));
      setSubmitting(false);
    }
  }

  return (
    <form className="auth-card" onSubmit={handleSubmit} noValidate>
      <CardHead
        step={1}
        title={t('forgotPassword.requestTitle')}
        lede={t('forgotPassword.requestLede')}
      />

      {error && <Alert onDismiss={() => setError(null)}>{describeFailure(error)}</Alert>}

      <div className="field">
        <label htmlFor="email">{t('forgotPassword.email')}</label>
        <input
          id="email"
          className="input"
          type="email"
          autoComplete="username"
          autoFocus
          placeholder={t('forgotPassword.emailPlaceholder')}
          value={email.value}
          onChange={(event) => email.setValue(event.target.value)}
          onBlur={email.touch}
          {...invalidProps('email', email.shown)}
        />
        <FieldError inputId="email" problem={email.shown} />
      </div>

      {wait > 0 ? (
        <Wait secondsLeft={wait} totalSeconds={waitTotal} message="forgotPassword.lockedWait" />
      ) : (
        <button type="submit" className="btn btn-primary btn-block" disabled={submitting}>
          {submitting ? t('forgotPassword.sending') : t('forgotPassword.sendCode')}
        </button>
      )}

      <p className="auth-foot">
        <Link to="/login">{t('forgotPassword.backToSignIn')}</Link>
      </p>
    </form>
  );
}

function VerifyStep({
  email,
  onVerified,
  onRestart,
}: {
  email: string;
  onVerified: (resetToken: string) => void;
  onRestart: () => void;
}) {
  const { t, tNode } = useI18n();
  const codeInput = useRef<HTMLInputElement>(null);
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);
  const [error, setError] = useState<FailedRequest | null>(null);
  // Starts full: arriving here means a code was just requested.
  const [resendIn, startResendTimer, resendTotal] = useCountdown(RESEND_COOLDOWN_SECONDS);
  const [lockedFor, startLock, lockTotal] = useCountdown();
  const locked = lockedFor > 0;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setResent(false);

    setSubmitting(true);
    try {
      const grant = await verifyPasswordResetCode(email, code);
      onVerified(grant.reset_token);
    } catch (caught) {
      // Any 429 here is the address's lockout or budget, not just the button.
      if (isTooSoon(caught)) startLock(caught.retryAfter);
      setError(failed(caught, 'forgotPassword.verifyFailed'));
      setCode('');
      setSubmitting(false);
      codeInput.current?.focus();
    }
  }

  async function handleResend() {
    setError(null);
    setResent(false);
    setResending(true);
    try {
      await requestPasswordReset(email);
      setResent(true);
      setCode('');
      startResendTimer(RESEND_COOLDOWN_SECONDS);
      codeInput.current?.focus();
    } catch (caught) {
      if (isTooSoon(caught)) {
        if (caught.code === 'auth.reset_locked') startLock(caught.retryAfter);
        else startResendTimer(caught.retryAfter);
      }
      setError(failed(caught, 'forgotPassword.requestFailed'));
    } finally {
      setResending(false);
    }
  }

  return (
    <form className="auth-card" onSubmit={handleSubmit} noValidate>
      <CardHead
        step={2}
        title={t('forgotPassword.verifyTitle')}
        lede={tNode('forgotPassword.verifyLede', { email: <strong>{email}</strong> })}
      />

      {error && <Alert onDismiss={() => setError(null)}>{describeFailure(error)}</Alert>}
      {resent && (
        <Alert kind="warn" onDismiss={() => setResent(false)}>
          {t('forgotPassword.resent')}
        </Alert>
      )}

      <div className="field">
        <label htmlFor="code">{t('forgotPassword.code')}</label>
        <CodeInput
          ref={codeInput}
          id="code"
          placeholder={t('forgotPassword.codePlaceholder')}
          autoFocus
          disabled={locked}
          value={code}
          onChange={setCode}
        />
        <p className="hint">
          {t('forgotPassword.codeHint', {
            minutes: CODE_TTL_MINUTES,
            attempts: MAX_ATTEMPTS,
          })}
        </p>
      </div>

      <button
        type="submit"
        className="btn btn-primary btn-block"
        disabled={submitting || locked || code.length !== 6}
      >
        {submitting ? t('forgotPassword.verifying') : t('forgotPassword.verify')}
      </button>

      <div className="auth-secondary">
        {locked ? (
          <Wait
            secondsLeft={lockedFor}
            totalSeconds={lockTotal}
            message="forgotPassword.lockedWait"
            tone="warn"
          />
        ) : resendIn > 0 ? (
          <Wait
            secondsLeft={resendIn}
            totalSeconds={resendTotal}
            message="forgotPassword.resendWait"
          />
        ) : (
          <button
            type="button"
            className="btn btn-secondary btn-block"
            disabled={resending}
            onClick={() => void handleResend()}
          >
            {resending ? t('forgotPassword.resending') : t('forgotPassword.resend')}
          </button>
        )}
      </div>

      <p className="auth-foot">
        <button type="button" className="btn-link" onClick={onRestart}>
          {t('forgotPassword.differentEmail')}
        </button>
        <span className="auth-foot-sep" aria-hidden="true">·</span>
        <Link to="/login">{t('forgotPassword.backToSignIn')}</Link>
      </p>
    </form>
  );
}

function ResetStep({
  email,
  resetToken,
  onDone,
  onRestart,
}: {
  email: string;
  resetToken: string;
  onDone: () => void;
  onRestart: () => void;
}) {
  const { t } = useI18n();
  const password = useField(checkNewPassword);
  const confirmation = useField((value) =>
    value === password.value ? null : 'errors.validation.passwordMismatch',
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<FailedRequest | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (
      !validateAll([
        [password, 'new-password'],
        [confirmation, 'confirm-password'],
      ])
    ) {
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword(resetToken, password.value);
      onDone();
    } catch (caught) {
      setError(failed(caught, 'forgotPassword.resetFailed'));
      setSubmitting(false);
    }
  }

  const spent =
    error?.cause instanceof ApiError && error.cause.code === 'auth.reset_token_invalid';

  return (
    <form className="auth-card" onSubmit={handleSubmit} noValidate>
      <CardHead
        step={3}
        title={t('forgotPassword.resetTitle')}
        lede={t('forgotPassword.resetLede')}
      />

      {error && (
        <Alert onDismiss={() => setError(null)}>
          {describeFailure(error)}
          {spent && (
            <>
              {' '}
              <button type="button" className="btn-link" onClick={onRestart}>
                {t('forgotPassword.startOver')}
              </button>
            </>
          )}
        </Alert>
      )}

      {/* Tells password managers which account the new password is for. */}
      <input type="email" autoComplete="username" value={email} readOnly hidden />

      <div className="field">
        <label htmlFor="new-password">{t('forgotPassword.newPassword')}</label>
        <input
          id="new-password"
          className="input"
          type="password"
          autoComplete="new-password"
          autoFocus
          placeholder={t('forgotPassword.newPasswordPlaceholder')}
          value={password.value}
          onChange={(event) => password.setValue(event.target.value)}
          onBlur={password.touch}
          {...invalidProps('new-password', password.shown)}
        />
        <FieldError inputId="new-password" problem={password.shown} />
      </div>

      <div className="field">
        <label htmlFor="confirm-password">{t('forgotPassword.confirmPassword')}</label>
        <input
          id="confirm-password"
          className="input"
          type="password"
          autoComplete="new-password"
          placeholder={t('forgotPassword.confirmPasswordPlaceholder')}
          value={confirmation.value}
          onChange={(event) => confirmation.setValue(event.target.value)}
          onBlur={confirmation.touch}
          {...invalidProps('confirm-password', confirmation.shown)}
        />
        <FieldError inputId="confirm-password" problem={confirmation.shown} />
      </div>

      <button type="submit" className="btn btn-primary btn-block" disabled={submitting}>
        {submitting ? t('forgotPassword.saving') : t('forgotPassword.save')}
      </button>

      <p className="auth-foot">
        <Link to="/login">{t('forgotPassword.backToSignIn')}</Link>
      </p>
    </form>
  );
}
