import type { ReactNode } from 'react';

interface AlertProps {
  kind?: 'error' | 'warn';
  children: ReactNode;
  onDismiss?: () => void;
}

export function Alert({ kind = 'error', children, onDismiss }: AlertProps) {
  return (
    <div className={`alert alert-${kind}`} role="alert">
      <div className="alert-body">{children}</div>
      {onDismiss && (
        <button type="button" className="alert-dismiss" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}
