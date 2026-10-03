import { useState } from 'react';

import type { MessageKey } from '../../i18n';
import type { Check } from '../validation';

export interface Field {
  value: string;
  setValue: (value: string) => void;
  problem: MessageKey | null;
  /** Only after the field is left or a submit is attempted. */
  shown: MessageKey | null;
  touch: () => void;
}

export function useField(check: Check, initial = ''): Field {
  const [value, setValue] = useState(initial);
  const [touched, setTouched] = useState(false);
  const problem = check(value);
  return {
    value,
    setValue,
    problem,
    shown: touched ? problem : null,
    touch: () => setTouched(true),
  };
}

/** Touches every field and focuses the first invalid one. */
export function validateAll(fields: Array<[Field, string]>): boolean {
  for (const [field] of fields) field.touch();
  const firstBad = fields.find(([field]) => field.problem);
  if (firstBad) document.getElementById(firstBad[1])?.focus();
  return !firstBad;
}
