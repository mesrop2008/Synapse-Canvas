import { ApiError } from './client';

/** Turns anything thrown by the API layer into something worth showing a user. */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (error instanceof ApiError) return error.message;
  // What fetch throws when it never got a response at all.
  if (error instanceof TypeError) {
    return 'Could not reach the API. Check that the backend is running.';
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
