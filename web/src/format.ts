const UNITS = ['kilobyte', 'megabyte', 'gigabyte'] as const;

/** "12 MB" / "12 МБ": Intl words the unit. From kilobytes up, in binary
 *  multiples as the limits are; a few bytes read better as "0.1 kB". */
export function formatBytes(bytes: number, locale: string): string {
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit: UNITS[unit],
    unitDisplay: 'short',
    maximumFractionDigits: value < 10 ? 1 : 0,
  }).format(value);
}
