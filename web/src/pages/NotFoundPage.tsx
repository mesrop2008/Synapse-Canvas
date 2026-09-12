import { Link } from 'react-router-dom';

import { Logo } from '../components/icons';

export function NotFoundPage() {
  return (
    <main className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <Logo size={26} />
          Synapse Canvas
        </div>
        <h1 className="auth-title">Not found</h1>
        <p className="auth-lede">There is nothing at this address.</p>
        <p className="auth-foot">
          <Link to="/workspaces">Back to your workspaces</Link>
        </p>
      </div>
    </main>
  );
}
