import { MonitorIcon, MoonIcon, SunIcon } from './icons';
import { useTheme, type ThemePreference } from '../hooks/useTheme';

const OPTIONS: Array<{
  value: ThemePreference;
  label: string;
  Icon: typeof SunIcon;
}> = [
  { value: 'light', label: 'Light', Icon: SunIcon },
  { value: 'system', label: 'Match system', Icon: MonitorIcon },
  { value: 'dark', label: 'Dark', Icon: MoonIcon },
];

/**
 * Three states rather than a two-way switch: 'system' is a real preference, and
 * collapsing it into light/dark means the app stops following the OS the first
 * time anyone touches the control.
 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={label}
          title={label}
          className="theme-toggle-option"
          onClick={() => setPreference(value)}
        >
          <Icon size={15} />
        </button>
      ))}
    </div>
  );
}
