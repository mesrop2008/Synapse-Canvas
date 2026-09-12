import type { SaveStatus } from '../hooks/useAutosave';

const LABELS: Record<SaveStatus, string> = {
  saved: 'Saved',
  saving: 'Saving…',
  unsaved: 'Unsaved changes',
  error: 'Not saved',
};

export function SaveIndicator({
  status,
  version,
}: {
  status: SaveStatus;
  version: number;
}) {
  return (
    <span
      className="save-indicator"
      data-state={status}
      role="status"
      aria-live="polite"
    >
      <span className="dot" aria-hidden="true" />
      {LABELS[status]}
      {status === 'saved' && <span className="subtle">· v{version}</span>}
    </span>
  );
}
