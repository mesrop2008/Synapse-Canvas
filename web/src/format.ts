const UNITS = ['byte', 'kilobyte', 'megabyte', 'gigabyte'] as const;

/** "12 MB" / "12 МБ": Intl words the unit. Binary multiples, as the limits are. */
export function formatBytes(bytes: number, locale: string): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit: UNITS[unit],
    unitDisplay: 'short',
    maximumFractionDigits: unit > 0 && value < 10 ? 1 : 0,
  }).format(value);
}
