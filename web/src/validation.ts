import type { MessageKey } from '../i18n';

export type Check = (value: string) => MessageKey | null;

// Stricter than type="email", which accepts a@g.c. Mirrors api/schemas/common.py.
const LOCAL_PART = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
const DOMAIN_LABEL = /^[\p{L}\p{N}](?:[\p{L}\p{N}-]{0,61}[\p{L}\p{N}])?$/u;
const TOP_LEVEL_DOMAIN = /^(?:\p{L}{2,63}|xn--[a-z0-9-]{1,59})$/iu;

export function isValidEmail(raw: string): boolean {
  const email = raw.trim();
  if (email.length > 254) return false;

  const at = email.lastIndexOf('@');
  if (at <= 0 || at !== email.indexOf('@')) return false;

  const local = email.slice(0, at);
  const labels = email.slice(at + 1).split('.');
  return (
    local.length <= 64 &&
    LOCAL_PART.test(local) &&
    labels.length >= 2 &&
    labels.every((label) => DOMAIN_LABEL.test(label)) &&
    TOP_LEVEL_DOMAIN.test(labels.at(-1) ?? '')
  );
}

export const checkEmail: Check = (value) => {
  if (!value.trim()) return 'errors.validation.emailRequired';
  return isValidEmail(value) ? null : 'errors.validation.emailInvalid';
};

export const checkRequired: Check = (value) =>
  value.trim() ? null : 'errors.validation.required';

export const checkNewPassword: Check = (value) => {
  if (!value) return 'errors.validation.required';
  if (value.length < 8) return 'errors.validation.passwordTooShort';
  // bcrypt ignores everything past 72 bytes; the API refuses longer ones.
  if (new TextEncoder().encode(value).length > 72) {
    return 'errors.validation.passwordTooLong';
  }
  return null;
};

export const checkCode: Check = (value) =>
  /^[0-9]{6}$/.test(value) ? null : 'errors.validation.codeIncomplete';
