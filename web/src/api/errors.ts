import { translate, type MessageKey } from '../../i18n';
import { ApiError } from './client';

/** An ApiError carries the server's `detail`, which stays English; only
 *  client-generated text comes from the packs. */
export function errorMessage(
  error: unknown,
  fallback: MessageKey = 'errors.generic',
): string {
  if (error instanceof ApiError) return error.message;
  // fetch throws TypeError when it never got a response at all.
  if (error instanceof TypeError) return translate('errors.unreachable');
  if (error instanceof Error && error.message) return error.message;
  return translate(fallback);
}
