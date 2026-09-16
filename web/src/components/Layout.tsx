import { Link, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';
import { LanguageToggle } from './LanguageToggle';
import { ThemeToggle } from './ThemeToggle';
import { LogOutIcon, Logo } from './icons';

export function Layout() {
  const { user, signOut } = useAuth();
  const { t } = useI18n();
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
            {t('common.appName')}
          </Link>

          <div className="topbar-actions">
            {/* The divider stops preferences and account reading as one row. */}
            <div className="topbar-group">
              <LanguageToggle />
              <ThemeToggle />
            </div>

            <span className="topbar-divider" aria-hidden="true" />

            <div className="topbar-group">
              {user && (
                <div className="user-chip">
                  <span className="avatar" aria-hidden="true">
                    {user.name.trim().charAt(0) || user.email.charAt(0)}
                  </span>
                  <span>{user.email}</span>
                </div>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                onClick={handleSignOut}
                title={t('layout.signOut')}
                aria-label={t('layout.signOut')}
              >
                <LogOutIcon />
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="content">
        <Outlet />
      </div>
    </div>
  );
}
