import { useI18n } from '../hooks/useI18n';
import type { Locale, MessageKey } from '../../i18n';

const LABELS: Record<Locale, { short: MessageKey; full: MessageKey }> = {
  en: { short: 'common.language.enShort', full: 'common.language.en' },
  ru: { short: 'common.language.ruShort', full: 'common.language.ru' },
};

export function LanguageToggle() {
  const { locale, locales, setLocale, t } = useI18n();

  return (
    <div className="segmented" role="radiogroup" aria-label={t('common.language.label')}>
      {locales.map((candidate) => (
        <button
          key={candidate}
          type="button"
          role="radio"
          aria-checked={locale === candidate}
          // The button reads "EN"; a screen reader should hear "English".
          aria-label={t(LABELS[candidate].full)}
          title={t(LABELS[candidate].full)}
          className="segmented-option segmented-text"
          onClick={() => setLocale(candidate)}
        >
          {t(LABELS[candidate].short)}
        </button>
      ))}
    </div>
  );
}
