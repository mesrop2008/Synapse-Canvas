import type { ReactNode } from 'react';

import { useI18n } from '../hooks/useI18n';

interface AlertProps {
  kind?: 'error' | 'warn';
  children: ReactNode;
  onDismiss?: () => void;
}

export function Alert({ kind = 'error', children, onDismiss }: AlertProps) {
  const { t } = useI18n();

  return (
    <div className={`alert alert-${kind}`} role="alert">
      <div className="alert-body">{children}</div>
      {onDismiss && (
        <button type="button" className="alert-dismiss" onClick={onDismiss}>
          {t('common.dismiss')}
        </button>
      )}
    </div>
  );
}
