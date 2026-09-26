/**
 * One folder per language, one JSON file per section of the site. The folder is
 * `rus`, but the locale code is `ru` -- the BCP-47 tag, which is what
 * `<html lang>` and Intl's plural rules expect.
 *
 * No React here: the fetch client and the error formatter need translations and
 * neither can call a hook.
 */

import enAccount from './en/account.json';
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

import ruAccount from './rus/account.json';
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
  account: enAccount,
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

/** The reference shape; every other pack is typed against it, so a missing key
 *  is a compile error rather than a blank label found in production. */
export type Messages = typeof english;

export const MESSAGES: Record<Locale, Messages> = {
  en: english,
  ru: {
    common: ruCommon,
    account: ruAccount,
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

/**
 * Walk a dotted path, or null if the pack has no string there.
 *
 * Takes a plain string rather than a `MessageKey`, because API error codes
 * arrive at runtime and cannot be checked at compile time. Everything else
 * should go through `template`, which is typed.
 */
export function lookup(locale: Locale, key: string): string | null {
  let node: unknown = MESSAGES[locale];
  for (const segment of key.split('.')) {
    if (typeof node !== 'object' || node === null) return null;
    node = (node as Record<string, unknown>)[segment];
  }
  return typeof node === 'string' ? node : null;
}

/** The raw template, before interpolation. */
export function template(locale: Locale, key: MessageKey): string {
  // Falls back to the key so a gap looks wrong rather than taking the page
  // down; `MessageKey` keeps that hypothetical.
  return lookup(locale, key) ?? key;
}

export const PLACEHOLDER = /\{(\w+)\}/g;

export function interpolate(source: string, params?: MessageParams): string {
  if (!params) return source;
  return source.replace(PLACEHOLDER, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

// The provider keeps this in step; it is not a second source of truth.
let activeLocale: Locale = 'en';

export function setActiveLocale(locale: Locale): void {
  activeLocale = locale;
}

export function getActiveLocale(): Locale {
  return activeLocale;
}

/** For code outside the component tree; components use `useI18n`, which
 *  re-renders on a language change. */
export function translate(key: MessageKey, params?: MessageParams): string {
  return interpolate(template(activeLocale, key), params);
}
