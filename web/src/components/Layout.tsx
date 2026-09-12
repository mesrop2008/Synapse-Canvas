import { Link, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../hooks/useAuth';
import { ThemeToggle } from './ThemeToggle';
import { LogOutIcon, Logo } from './icons';

export function Layout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await signOut();
    navigate('/login', { replace: true });
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/workspaces" className="brand">
            <Logo />
            Synapse Canvas
          </Link>

          <div className="topbar-actions">
            <ThemeToggle />
            {user && (
              <div className="user-chip">
                <span>{user.email}</span>
                <span className="avatar" aria-hidden="true">
                  {user.name.trim().charAt(0) || user.email.charAt(0)}
                </span>
              </div>
            )}
            <button
              type="button"
              className="btn btn-ghost btn-icon"
              onClick={handleSignOut}
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOutIcon />
            </button>
          </div>
        </div>
      </header>

      <div className="content">
        <Outlet />
      </div>
    </div>
  );
}
