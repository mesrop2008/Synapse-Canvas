import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { verifyEmail } from '../api/auth';
import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';

type State = 'missing' | 'verifying' | 'verified' | 'failed';

/**
 * Landing page for the emailed link. EMAIL_VERIFICATION_LINK_BASE on the
 * backend has to point here, or the token never reaches /auth/verify-email.
 */
export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token');

  const [state, setState] = useState<State>(token ? 'verifying' : 'missing');
  const [error, setError] = useState<string | null>(null);
  // The token is single-use, so a second POST would fail even though the first
  // succeeded -- and StrictMode runs this effect twice in development.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    verifyEmail(token)
      .then(() => setState('verified'))
      .catch((caught: unknown) => {
        setError(errorMessage(caught, 'That link could not be used.'));
        setState('failed');
      });
  }, [token]);

  return (
    <main className="auth-shell">
      <div className="card auth-card">
        <h1>Email verification</h1>

        {state === 'missing' && (
          <p>This link is missing its token. Use the link from the email as-is.</p>
        )}

        {state === 'verifying' && <p className="subtle">Checking the link…</p>}

        {state === 'verified' && (
          <p>Your address is confirmed. You can sign in now.</p>
        )}

        {state === 'failed' && (
          <>
            <Alert>{error}</Alert>
            <p className="subtle">
              Links expire and can only be used once. Registering again will send
              a fresh one.
            </p>
          </>
        )}

        <p className="subtle" style={{ marginBottom: 0 }}>
          <Link to="/login">Go to sign in</Link>
        </p>
      </div>
    </main>
  );
}
