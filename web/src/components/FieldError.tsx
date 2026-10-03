import type { MessageKey } from '../../i18n';
import { useI18n } from '../hooks/useI18n';

export function fieldErrorId(inputId: string): string {
  return `${inputId}-error`;
}

/** Spread onto the input the error describes. */
export function invalidProps(inputId: string, problem: MessageKey | null) {
  return problem
    ? { 'aria-invalid': true, 'aria-describedby': fieldErrorId(inputId) }
    : {};
}

export function FieldError({ inputId, problem }: { inputId: string; problem: MessageKey | null }) {
  const { t } = useI18n();
  if (!problem) return null;
  return (
    <p className="field-error" id={fieldErrorId(inputId)} role="alert">
      {t(problem)}
    </p>
  );
}
