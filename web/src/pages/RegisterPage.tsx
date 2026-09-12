import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';

import { register, resendVerification } from '../api/auth';
import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
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
      <main className="auth-shell">
        <div className="card auth-card">
          <h1>Check your email</h1>
          <p>
            If <strong>{email}</strong> can receive mail, a verification link is
            on its way. You will be able to sign in once you have followed it.
          </p>
          <p className="subtle">
            Running locally, the backend logs the link to its console instead of
            sending it — look for <code>[email:console]</code> in the API output.
          </p>
          <button
            type="button"
            disabled={resent}
            onClick={() => {
              void resendVerification(email).catch(() => undefined);
              setResent(true);
            }}
          >
            {resent ? 'Link re-sent' : 'Send it again'}
          </button>
          <p className="subtle" style={{ marginBottom: 0 }}>
            <Link to="/login">Back to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-shell">
      <form className="card auth-card" onSubmit={handleSubmit}>
        <h1>Create an account</h1>

        {error && <Alert onDismiss={() => setError(null)}>{error}</Alert>}

        <div className="field">
          <label htmlFor="name">Name</label>
          <input
            id="name"
            type="text"
            autoComplete="name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <span className="subtle">At least 8 characters.</span>
        </div>

        <button type="submit" className="primary block" disabled={submitting}>
          {submitting ? 'Creating…' : 'Create account'}
        </button>

        <p className="subtle" style={{ marginBottom: 0 }}>
          Already registered? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </main>
  );
}
