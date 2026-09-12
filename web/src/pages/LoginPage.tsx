import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ThemeToggle } from '../components/ThemeToggle';
import { Logo } from '../components/icons';
import { useAuth } from '../hooks/useAuth';

export function LoginPage() {
  const { status, signIn } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Set by ProtectedRoute when it intercepted a deep link.
  const from = (location.state as { from?: string } | null)?.from ?? '/workspaces';

  if (status === 'loading') {
    return <p className="placeholder">Restoring your session…</p>;
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
      setError(errorMessage(caught, 'Could not sign in.'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth">
      <div className="auth-theme">
        <ThemeToggle />
      </div>

      <div>
        <form className="auth-card" onSubmit={handleSubmit}>
          <div className="auth-brand">
            <Logo size={24} />
            Synapse Canvas
          </div>

          <h1 className="auth-title">Welcome back</h1>
          <p className="auth-lede">Sign in to your workspaces.</p>

          {error && <Alert onDismiss={() => setError(null)}>{error}</Alert>}

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
              autoComplete="current-password"
              placeholder="••••••••"
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
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="auth-foot">
            No account yet? <Link to="/register">Create one</Link>
          </p>
        </form>
      </div>
    </main>
  );
}
