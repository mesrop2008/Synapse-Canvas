import { Link, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../hooks/useAuth';

export function Layout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await signOut();
    navigate('/login', { replace: true });
  }

  return (
    <>
      <header className="app-header">
        <Link to="/workspaces" className="brand">
          Synapse Canvas
        </Link>
        <div className="who">
          {user && <span>{user.email}</span>}
          <button type="button" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>
      <Outlet />
    </>
  );
}
