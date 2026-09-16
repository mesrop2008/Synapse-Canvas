import { Link } from 'react-router-dom';

import { Logo } from '../components/icons';
import { useI18n } from '../hooks/useI18n';

export function NotFoundPage() {
  const { t } = useI18n();

  return (
    <main className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <Logo size={26} />
          {t('common.appName')}
        </div>
        <h1 className="auth-title">{t('notFound.title')}</h1>
        <p className="auth-lede">{t('notFound.lede')}</p>
        <p className="auth-foot">
          <Link to="/workspaces">{t('notFound.back')}</Link>
        </p>
      </div>
    </main>
  );
}
