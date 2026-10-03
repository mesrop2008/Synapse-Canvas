import { getActiveLocale, lookup, translate, type MessageKey } from '../../i18n';
import { ApiError } from './client';

/** Worded at render time, so a language switch reaches errors on screen. */
export interface FailedRequest {
  cause: unknown;
  fallback: MessageKey;
}

export function failed(
  cause: unknown,
  fallback: MessageKey = 'errors.generic',
): FailedRequest {
  return { cause, fallback };
}

export function describeFailure(failure: FailedRequest): string {
  return errorMessage(failure.cause, failure.fallback);
}

/** Translates `code`; the English `detail` is the fallback for a code this
 *  client does not know yet. */
export function errorMessage(
  error: unknown,
  fallback: MessageKey = 'errors.generic',
): string {
  if (error instanceof ApiError) {
    if (error.code) {
      const translated = lookup(getActiveLocale(), `errors.api.${error.code}`);
      if (translated) return translated;
    }
    return error.message;
  }
  // fetch throws TypeError when it never got a response at all.
  if (error instanceof TypeError) return translate('errors.unreachable');
  if (error instanceof Error && error.message) return error.message;
  return translate(fallback);
}
