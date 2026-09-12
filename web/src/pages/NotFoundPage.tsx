import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <main className="auth-shell">
      <div className="card auth-card">
        <h1>Not found</h1>
        <p className="subtle">There is nothing at this address.</p>
        <p style={{ marginBottom: 0 }}>
          <Link to="/workspaces">Back to your workspaces</Link>
        </p>
      </div>
    </main>
  );
}
