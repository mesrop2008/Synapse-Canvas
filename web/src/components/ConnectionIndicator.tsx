import { useI18n } from '../hooks/useI18n';
import type { ConnectionState } from '../hooks/useDocumentSocket';
import type { MessageKey } from '../../i18n';

const LABELS: Record<ConnectionState, MessageKey> = {
  connecting: 'document.connection.connecting',
  live: 'document.connection.live',
  reconnecting: 'document.connection.reconnecting',
  offline: 'document.connection.offline',
};

export function ConnectionIndicator({
  state,
  version,
}: {
  state: ConnectionState;
  version: number;
}) {
  const { t } = useI18n();

  return (
    <span
      className="connection-indicator"
      data-state={state}
      role="status"
      aria-live="polite"
    >
      <span className="dot" aria-hidden="true" />
      {t(LABELS[state])}
      {state === 'live' && (
        <span className="muted">{t('document.connection.version', { version })}</span>
      )}
    </span>
  );
}
