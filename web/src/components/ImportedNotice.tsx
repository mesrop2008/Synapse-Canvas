import { lookup } from '../../i18n';
import { useI18n } from '../hooks/useI18n';
import { Alert } from './Alert';

/** Codes for what an imported file held that the editor cannot. */
export function droppedFrom(state: unknown): string[] {
  const dropped = (state as { dropped?: unknown } | null)?.dropped;
  return Array.isArray(dropped) ? dropped.filter((code) => typeof code === 'string') : [];
}

/** What a Word import left out, in the reader's language. A code this
 *  client does not know yet still counts, as "other content". */
export function ImportedNotice({ dropped, onDismiss }: { dropped: string[]; onDismiss: () => void }) {
  const { t, locale } = useI18n();
  const names = dropped.map(
    (code) =>
      lookup(locale, `document.import.features.${code}`) ?? t('document.import.features.other'),
  );
  const features = new Intl.ListFormat(locale, { type: 'conjunction' }).format([
    ...new Set(names),
  ]);

  return (
    <Alert kind="warn" onDismiss={onDismiss}>
      {t('document.import.dropped', { features })}
    </Alert>
  );
}
