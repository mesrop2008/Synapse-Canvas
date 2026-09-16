import { useI18n } from '../hooks/useI18n';
import type { SaveStatus } from '../hooks/useAutosave';
import type { MessageKey } from '../../i18n';

const LABELS: Record<SaveStatus, MessageKey> = {
  saved: 'document.save.saved',
  saving: 'document.save.saving',
  unsaved: 'document.save.unsaved',
  error: 'document.save.error',
};

export function SaveIndicator({
  status,
  version,
}: {
  status: SaveStatus;
  version: number;
}) {
  const { t } = useI18n();

  return (
    <span
      className="save-indicator"
      data-state={status}
      role="status"
      aria-live="polite"
    >
      <span className="dot" aria-hidden="true" />
      {t(LABELS[status])}
      {status === 'saved' && (
        <span className="muted">{t('document.save.version', { version })}</span>
      )}
    </span>
  );
}
