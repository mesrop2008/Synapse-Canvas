import type { ReactNode } from 'react';

interface AlertProps {
  kind?: 'error' | 'warn';
  children: ReactNode;
  onDismiss?: () => void;
}

export function Alert({ kind = 'error', children, onDismiss }: AlertProps) {
  return (
    <div className={`alert alert-${kind}`} role="alert">
      {onDismiss && (
        <button type="button" className="link dismiss" onClick={onDismiss}>
          Dismiss
        </button>
      )}
      {children}
    </div>
  );
}
