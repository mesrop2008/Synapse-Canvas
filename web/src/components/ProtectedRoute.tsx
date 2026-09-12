import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { useAuth } from '../hooks/useAuth';

/**
 * Gate for the signed-in half of the app.
 *
 * The 'loading' state is what stops a page reload from bouncing a signed-in
 * user to /login: restoring the session needs a round trip, and until it
 * finishes nobody knows which they are.
 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return <p className="placeholder">Restoring your session…</p>;
  }

  if (status === 'anonymous') {
    // `from` lets the login page send them back where they were headed.
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  return <>{children}</>;
}
