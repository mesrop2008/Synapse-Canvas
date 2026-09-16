import { MoonIcon, SunIcon } from './icons';
import { useTheme, type Theme } from '../hooks/useTheme';
import { useI18n } from '../hooks/useI18n';
import type { MessageKey } from '../../i18n';

const OPTIONS: Array<{ value: Theme; label: MessageKey; Icon: typeof SunIcon }> = [
  { value: 'light', label: 'common.theme.light', Icon: SunIcon },
  { value: 'dark', label: 'common.theme.dark', Icon: MoonIcon },
];

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const { t } = useI18n();

  return (
    <div className="segmented" role="radiogroup" aria-label={t('common.theme.label')}>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          aria-label={t(label)}
          title={t(label)}
          className="segmented-option"
          onClick={() => setTheme(value)}
        >
          <Icon size={16} />
        </button>
      ))}
    </div>
  );
}
