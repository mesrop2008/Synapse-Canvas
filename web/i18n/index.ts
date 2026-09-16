/**
 * Language packs and the lookup around them.
 *
 * One folder per language, one JSON file per section of the site. The folder is
 * `rus`, but the locale *code* stays `ru`: that is the BCP-47 tag, and it is
 * what `<html lang>` and Intl's plural rules expect.
 *
 * No React here on purpose -- the fetch client and the error formatter need
 * translations too, and neither can call a hook.
 */

import enCommon from './en/common.json';
import enDocument from './en/document.json';
import enErrors from './en/errors.json';
import enLayout from './en/layout.json';
import enLogin from './en/login.json';
import enNotFound from './en/not-found.json';
import enRegister from './en/register.json';
import enVerifyEmail from './en/verify-email.json';
import enWorkspace from './en/workspace.json';
import enWorkspaces from './en/workspaces.json';

import ruCommon from './rus/common.json';
import ruDocument from './rus/document.json';
import ruErrors from './rus/errors.json';
import ruLayout from './rus/layout.json';
import ruLogin from './rus/login.json';
import ruNotFound from './rus/not-found.json';
import ruRegister from './rus/register.json';
import ruVerifyEmail from './rus/verify-email.json';
import ruWorkspace from './rus/workspace.json';
import ruWorkspaces from './rus/workspaces.json';

export const LOCALES = ['en', 'ru'] as const;
export type Locale = (typeof LOCALES)[number];

/** Filenames are kebab-case; the namespace they are addressed by is not. */
const english = {
  common: enCommon,
  layout: enLayout,
  login: enLogin,
  register: enRegister,
  verifyEmail: enVerifyEmail,
  notFound: enNotFound,
  workspaces: enWorkspaces,
  workspace: enWorkspace,
  document: enDocument,
  errors: enErrors,
};

/** English is the reference shape; every other pack is checked against it. */
export type Messages = typeof english;

/**
 * Typing the record against `Messages` is what makes a half-translated pack a
 * compile error rather than a blank label someone notices in production.
 */
export const MESSAGES: Record<Locale, Messages> = {
  en: english,
  ru: {
    common: ruCommon,
    layout: ruLayout,
    login: ruLogin,
    register: ruRegister,
    verifyEmail: ruVerifyEmail,
    notFound: ruNotFound,
    workspaces: ruWorkspaces,
    workspace: ruWorkspace,
    document: ruDocument,
    errors: ruErrors,
  },
};

/** Dotted paths to every string in a pack, e.g. `login.title`. */
type DottedKeys<T> = {
  [K in keyof T & string]: T[K] extends string ? K : `${K}.${DottedKeys<T[K]>}`;
}[keyof T & string];

export type MessageKey = DottedKeys<Messages>;

export type MessageParams = Record<string, string | number>;

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value);
}

/** The raw template, before interpolation. */
export function template(locale: Locale, key: MessageKey): string {
  let node: unknown = MESSAGES[locale];
  for (const segment of key.split('.')) {
    if (typeof node !== 'object' || node === null) break;
    node = (node as Record<string, unknown>)[segment];
  }
  // Falling back to the key rather than throwing: a missing string should look
  // wrong, not take the page down. `MessageKey` keeps that hypothetical.
  return typeof node === 'string' ? node : key;
}

export const PLACEHOLDER = /\{(\w+)\}/g;

export function interpolate(source: string, params?: MessageParams): string {
  if (!params) return source;
  return source.replace(PLACEHOLDER, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

// Module state so non-React callers get the language the user actually picked.
// The provider keeps it in step; it is not a second source of truth.
let activeLocale: Locale = 'en';

export function setActiveLocale(locale: Locale): void {
  activeLocale = locale;
}

export function getActiveLocale(): Locale {
  return activeLocale;
}

/** For code outside the component tree. Components use `useI18n`, which
 *  re-renders them when the language changes. */
export function translate(key: MessageKey, params?: MessageParams): string {
  return interpolate(template(activeLocale, key), params);
}
