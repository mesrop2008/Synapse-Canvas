import { translate, type MessageKey } from '../../i18n';
import { ApiError } from './client';

/**
 * Turns anything thrown by the API layer into something worth showing a user.
 *
 * An ApiError's message is the server's `detail`, which is English whatever the
 * UI language is -- translating the backend is its own job, not this one's.
 * Everything the client generates itself comes from the language packs.
 */
export function errorMessage(
  error: unknown,
  fallback: MessageKey = 'errors.generic',
): string {
  if (error instanceof ApiError) return error.message;
  // What fetch throws when it never got a response at all.
  if (error instanceof TypeError) return translate('errors.unreachable');
  if (error instanceof Error && error.message) return error.message;
  return translate(fallback);
}
