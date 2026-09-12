import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';

import { register, resendVerification } from '../api/auth';
import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';

export function RegisterPage() {
  const { status } = useAuth();

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
      setError(errorMessage(caught, 'Could not register.'));
    } finally {
      setSubmitting(false);
    }
  }

  // The backend answers 202 whether or not the address was already taken, and
  // refuses login until the address is verified, so there is nothing to sign
  // the user into here -- only somewhere to send them.
  if (submitted) {
    return (
      <main className="auth">
        <div className="auth-theme">
          <ThemeToggle />
        </div>

        <div className="auth-card">
          <div className="auth-brand">
            <Logo size={26} />
            Synapse Canvas
          </div>

          <h1 className="auth-title">Check your email</h1>
          <p className="auth-lede">
            If <strong>{email}</strong> can receive mail, a verification link is on
            its way. You can sign in once you have followed it.
          </p>

          <p className="note">
            Running locally, the backend logs the link instead of sending it — look
            for <code>[email:console]</code> in the API output.
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
              {resent ? 'Link re-sent' : 'Send it again'}
            </button>
          </div>

          <p className="auth-foot">
            <Link to="/login">Back to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="auth">
      <div className="auth-theme">
        <ThemeToggle />
      </div>

      <form className="auth-card" onSubmit={handleSubmit}>
        <div className="auth-brand">
          <Logo size={26} />
          Synapse Canvas
        </div>

        <h1 className="auth-title">Create an account</h1>
        <p className="auth-lede">Start writing with your team.</p>

        {error && <Alert onDismiss={() => setError(null)}>{error}</Alert>}

        <div className="field">
          <label htmlFor="name">Name</label>
          <input
            id="name"
            className="input"
            type="text"
            autoComplete="name"
            placeholder="Ada Lovelace"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            placeholder="you@example.com"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
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
          {submitting ? 'Creating…' : 'Create account'}
        </button>

        <p className="auth-foot">
          Already registered? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </main>
  );
}
