import { MonitorIcon, MoonIcon, SunIcon } from './icons';
import { useTheme, type ThemePreference } from '../hooks/useTheme';
import { useI18n } from '../hooks/useI18n';
import type { MessageKey } from '../../i18n';

const OPTIONS: Array<{
  value: ThemePreference;
  label: MessageKey;
  Icon: typeof SunIcon;
}> = [
  { value: 'light', label: 'common.theme.light', Icon: SunIcon },
  { value: 'system', label: 'common.theme.system', Icon: MonitorIcon },
  { value: 'dark', label: 'common.theme.dark', Icon: MoonIcon },
];

/**
 * Three states rather than a two-way switch: 'system' is a real preference, and
 * collapsing it into light/dark means the app stops following the OS the first
 * time anyone touches the control.
 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  const { t } = useI18n();

  return (
    <div className="segmented" role="radiogroup" aria-label={t('common.theme.label')}>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={t(label)}
          title={t(label)}
          className="segmented-option"
          onClick={() => setPreference(value)}
        >
          <Icon size={16} />
        </button>
      ))}
    </div>
  );
}
