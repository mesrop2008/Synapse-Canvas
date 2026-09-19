import { getActiveLocale, lookup, translate, type MessageKey } from '../../i18n';
import { ApiError } from './client';

/**
 * The API sends a stable `code` and an English `detail`. The code is what gets
 * translated; the detail is the fallback for a failure this client has no
 * wording for yet -- a newer server, or a response from something in front of
 * it. Showing the English sentence beats showing a raw identifier.
 */
/**
 * A failure held for later display.
 *
 * What failed, not what to say about it: the wording is chosen at render time,
 * so switching language reaches an error that is already on screen. Storing the
 * translated string instead would freeze it in whichever language was active
 * when the request failed.
 */
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
