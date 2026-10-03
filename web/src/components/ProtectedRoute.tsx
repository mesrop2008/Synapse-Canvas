import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { useAuth } from '../hooks/useAuth';
import { useI18n } from '../hooks/useI18n';

/** 'loading' keeps a reload from bouncing a signed-in user to /login. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const { t } = useI18n();
  const location = useLocation();

  if (status === 'loading') {
    return <p className="placeholder">{t('layout.restoringSession')}</p>;
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  return <>{children}</>;
}
